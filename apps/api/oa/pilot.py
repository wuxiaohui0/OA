import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from .calendar import clip, hours, subtract
from .db import camel_row
from .domain import now_iso, parse_time, require, utc_iso
from .organization import OrganizationService


class PilotService:
    def __init__(self, db, leaves):
        self.db, self.leaves = db, leaves

    def view(self, request_id, user):
        leave = self.db.leave(request_id)
        require(leave, "请假申请不存在", 404)
        involved = self.db.one("SELECT id FROM approval_steps WHERE request_id=? AND approver_id=?", request_id, user["id"])
        cancellation = self.db.one("SELECT id FROM leave_cancellations WHERE request_id=? AND approver_id=?", request_id, user["id"])
        former = self.db.one("""SELECT a.user_id FROM cancellation_assignees a JOIN leave_cancellations c
            ON c.id=a.cancellation_id WHERE c.request_id=? AND a.user_id=?""", request_id, user["id"])
        require(leave["applicantId"] == user["id"] or leave["currentApproverId"] == user["id"]
                or user["permissions"]["manageOrganization"] or involved or cancellation or former, "没有权限查看该申请", 403)
        return leave

    def details(self, leave):
        reviews = self.db.all("SELECT * FROM agent_reviews WHERE request_id=? ORDER BY id DESC LIMIT 1", leave["id"])
        review = camel_row(reviews[0]) if reviews and leave["agentDecision"] else None
        if review:
            for key in ("missingFields", "riskFlags", "policyReferences"):
                review[key] = json.loads(review[key])
        return {"leave": leave, "actions": self.db.actions(leave["id"]), "approvalSteps": self.db.steps(leave["id"]),
                "attachments": self.attachments(leave["id"]), "cancellations": self.cancellations(leave["id"]),
                "review": review}

    def calendar(self):
        return [camel_row(r) for r in self.db.all("SELECT * FROM work_calendar ORDER BY day")]

    def save_day(self, user, data):
        OrganizationService.require_access(user)
        try:
            day = date.fromisoformat(data["day"]).isoformat()
        except ValueError:
            require(False, "日历日期格式应为 YYYY-MM-DD")
        with self.db.transaction():
            before = self.db.one("SELECT * FROM work_calendar WHERE day=?", day)
            self.db.execute("""INSERT INTO work_calendar VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET
                is_workday=excluded.is_workday,name=excluded.name,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                day, int(data["isWorkday"]), data["name"], user["id"], now_iso())
            self.db.record_organization("calendar:" + day, user, "update_calendar", before, data)
        return self.calendar()

    def delete_day(self, user, day):
        OrganizationService.require_access(user)
        with self.db.transaction():
            before = self.db.one("SELECT * FROM work_calendar WHERE day=?", day)
            require(before, "日历设置不存在", 404)
            self.db.execute("DELETE FROM work_calendar WHERE day=?", day)
            self.db.record_organization("calendar:" + day, user, "reset_calendar", before, {})

    def ledger(self, user, employee_id):
        require(employee_id == user["id"] or user["permissions"]["manageOrganization"], "没有权限查看他人台账", 403)
        require(self.db.user(employee_id), "员工不存在", 404)
        return {"balances": self.db.balances(employee_id), "entries": [camel_row(r) for r in self.db.all(
            "SELECT * FROM balance_ledger WHERE user_id=? ORDER BY id DESC", employee_id)]}

    def adjust_balance(self, user, data):
        OrganizationService.require_access(user)
        delta = data["hours"]
        require(delta != 0 and round(delta, 2) == delta, "调整额度必须非零，最多两位小数")
        with self.db.transaction():
            key = "adjust:" + data["operationId"]
            existing = self.db.one("SELECT * FROM balance_ledger WHERE entry_key=?", key)
            if existing:
                require(existing["user_id"] == data["userId"] and existing["leave_type"] == data["leaveType"]
                        and existing["total_delta"] == delta and existing["reason"] == data["reason"]
                        and existing["actor_id"] == user["id"], "操作编号已被使用", 409)
                return self.ledger(user, data["userId"])
            target = self.db.user(data["userId"])
            require(target, "员工不存在", 404)
            changed = self.db.execute("""UPDATE leave_balances SET total_hours=ROUND(total_hours+?,2)
                WHERE user_id=? AND leave_type=? AND total_hours+?>=used_hours AND total_hours+?<=2000""",
                delta, data["userId"], data["leaveType"], delta, delta)
            require(changed.rowcount == 1, "调整后额度不能低于已用额度或超过 2000 小时", 409)
            self.db.record_balance(key, data["userId"], data["leaveType"], delta, 0, data["reason"], user["id"], user["name"])
            self.db.notify(data["userId"], None, "假期额度已调整", "HR 已调整你的假期额度，可在假期台账查看原因。")
            return self.ledger(user, data["userId"])

    def cancellations(self, request_id):
        return [camel_row(r) for r in self.db.all("""SELECT c.*,u.name AS approver_name FROM leave_cancellations c
            JOIN users u ON u.id=c.approver_id WHERE request_id=? ORDER BY c.created_at DESC""", request_id)]

    def cancellation_inbox(self, user):
        return [camel_row(r) for r in self.db.all("""SELECT c.*,lr.applicant_id,u.name AS applicant_name,
            lr.leave_type FROM leave_cancellations c JOIN leave_requests lr ON lr.id=c.request_id
            JOIN users u ON u.id=lr.applicant_id WHERE c.approver_id=? AND c.status='pending' ORDER BY c.created_at""", user["id"])]

    def cancel(self, request_id, user, data):
        with self.db.transaction():
            leave = self.leaves.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能申请自己的销假", 403)
            self.leaves.check_version(leave, data["version"])
            require(leave["status"] == "approved", "只有已批准的申请可以销假")
            start, end = parse_time(data["startAt"]), parse_time(data["endAt"])
            require(parse_time(leave["startAt"]) <= start < end <= parse_time(leave["endAt"]), "销假时段必须在原请假范围内")
            start_at, end_at = utc_iso(start.isoformat()), utc_iso(end.isoformat())
            amount = hours(clip(leave["workSegments"], start_at, end_at))
            require(amount > 0, "销假时段没有覆盖原申请的工作时段")
            require(not self.db.one("""SELECT id FROM leave_cancellations WHERE request_id=? AND status IN ('pending','approved')
                AND start_at<? AND end_at>?""", request_id, end_at, start_at), "销假时段与已有销假申请重叠", 409)
            # Legacy approvals lack reliable debit evidence; HR must reconcile them rather than silently refund.
            require(self.db.one("SELECT id FROM balance_ledger WHERE entry_key=?", "deduct:" + request_id),
                    "此历史申请没有可核对的扣减流水，请由 HR 核对并调整额度后记录处理结果", 409)
            manager = OrganizationService(self.db).require_approver(self.db.user(user["id"]))
            key = str(uuid4())
            self.db.execute("""INSERT INTO leave_cancellations(id,request_id,start_at,end_at,hours,reason,approver_id,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", key, request_id, start_at, end_at, amount, data["reason"], manager["id"], now_iso())
            self.db.execute("INSERT INTO cancellation_assignees VALUES(?,?)", key, manager["id"])
            self.touch(leave)
            self.action(leave, user, "cancel_request", f"申请销假 {amount:g} 小时：{data['reason']}")
            self.db.notify(manager["id"], request_id, "有新的销假待审批", f"{user['name']}申请销假 {amount:g} 小时。")
            return self.db.leave(request_id)

    def decide_cancel(self, key, user, decision, reason):
        with self.db.transaction():
            item = self.db.one("SELECT * FROM leave_cancellations WHERE id=?", key)
            require(item, "销假申请不存在", 404)
            leave = self.leaves.require_leave(item["request_id"])
            require(item["status"] == "pending", "销假申请已处理", 409)
            if decision == "withdrawn":
                require(leave["applicantId"] == user["id"], "只能撤回自己的销假申请", 403)
            else:
                require(item["approver_id"] == user["id"] and user["permissions"]["approveLeave"]
                        and user["id"] != leave["applicantId"], "没有权限审批此销假申请", 403)
            self.db.execute("UPDATE leave_cancellations SET status=?,comment=?,reviewed_at=? WHERE id=? AND status='pending'",
                            decision, reason, now_iso(), key)
            if decision == "approved":
                require(leave["status"] == "approved", "原申请已变更，不能重复返还", 409)
                approved = self.db.all("SELECT * FROM leave_cancellations WHERE request_id=? AND status='approved'", leave["id"])
                remaining = leave["workSegments"]
                for cancellation in approved:
                    remaining = subtract(remaining, cancellation["start_at"], cancellation["end_at"])
                refunded = round(leave["durationHours"] - hours(remaining), 2)
                # Round cumulative usage rather than each slice; the final slice absorbs cents.
                item["hours"] = round(refunded - sum(c["hours"] for c in approved if c["id"] != key), 2)
                require(item["hours"] >= 0, "销假台账异常，请联系 HR 核对", 409)
                self.db.execute("UPDATE leave_cancellations SET hours=? WHERE id=?", item["hours"], key)
                require(refunded <= leave["durationHours"] + 0.001, "返还时长超过原申请", 409)
                changed = self.db.execute("""UPDATE leave_balances SET used_hours=ROUND(used_hours-?,2)
                    WHERE user_id=? AND leave_type=? AND used_hours>=?""", item["hours"], leave["applicantId"], leave["leaveType"], item["hours"])
                require(changed.rowcount == 1, "余额台账异常，请联系 HR 核对", 409)
                self.db.record_balance("refund:" + key, leave["applicantId"], leave["leaveType"], 0, -item["hours"],
                                       "销假返还：" + reason, user["id"], user["name"], leave["id"])
                if not remaining:
                    self.db.transition(leave["id"], "cancelled", "human", user["id"], user["name"], "cancel_complete", "已全部销假")
            self.touch(leave)
            self.action(self.db.leave(leave["id"]), user, "cancel_" + decision, reason)
            target = item["approver_id"] if decision == "withdrawn" else leave["applicantId"]
            self.db.notify(target, leave["id"], "销假申请已更新", "请查看销假处理结果及额度变动。")
            return self.db.leave(leave["id"])

    def touch(self, leave):
        self.db.execute("UPDATE leave_requests SET version=version+1,updated_at=? WHERE id=?", now_iso(), leave["id"])

    def action(self, leave, user, action, reason):
        self.db.record_action(leave["id"], "human", user["id"], user["name"], action, reason, leave["status"], leave["status"])

    def transfer(self, request_id, user, data, cancellation_id=None):
        with self.db.transaction():
            leave = self.leaves.require_leave(request_id)
            self.leaves.check_version(leave, data["version"])
            item = None
            if cancellation_id:
                item = self.db.one("SELECT * FROM leave_cancellations WHERE id=? AND request_id=? AND status='pending'", cancellation_id, request_id)
                require(item, "待处理销假申请不存在", 404)
            else:
                require(leave["status"] == "human_reviewing", "只能转交待人工审批的申请")
            owner = item["approver_id"] if item else leave["currentApproverId"]
            require(user["id"] == owner or user["permissions"]["manageOrganization"], "没有权限转交审批", 403)
            approver = OrganizationService(self.db).leave_approver(data["approverId"])
            require(approver["id"] not in (leave["applicantId"], owner), "不能转交给申请人或当前审批人")
            if item:
                for participant in (owner, approver["id"]):
                    self.db.execute("INSERT OR IGNORE INTO cancellation_assignees VALUES(?,?)", cancellation_id, participant)
                self.db.execute("UPDATE leave_cancellations SET approver_id=? WHERE id=?", approver["id"], cancellation_id)
            else:
                self.db.complete_step(request_id, owner, "skipped", "转交：" + data["reason"])
                self.db.execute("UPDATE leave_requests SET current_approver_id=? WHERE id=?", approver["id"], request_id)
                self.db.ensure_step(request_id, approver)
            self.touch(leave)
            self.action(leave, user, "transfer", f"{'销假' if item else '请假'}审批转交给 {approver['name']}：{data['reason']}")
            for target in {owner, approver["id"], leave["applicantId"]}:
                self.db.notify(target, request_id, "审批已转交", f"当前审批人为 {approver['name']}。")
            return self.db.leave(request_id)

    def attachments(self, request_id):
        return [camel_row(r) for r in self.db.all("SELECT id,request_id,uploader_id,filename,mime_type,size,created_at FROM leave_attachments WHERE request_id=? ORDER BY created_at", request_id)]

    def upload(self, request_id, user, filename, content):
        with self.db.transaction():
            leave = self.leaves.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能为自己的申请上传材料", 403)
            require(leave["status"] in ("draft", "need_information", "rejected", "withdrawn"), "请先撤回或退回补充后上传材料")
            require(0 < len(content) <= 5 * 1024 * 1024, "文件大小应在 1 字节至 5 MB 之间")
            filename = filename.replace("\\", "/").split("/")[-1]
            require(1 <= len(filename) <= 150 and not any(ord(c) < 32 or ord(c) == 127 for c in filename), "文件名无效")
            suffix = filename.rsplit(".", 1)[-1].lower()
            valid = (suffix == "pdf" and content.startswith(b"%PDF-")) or (suffix == "png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or (suffix in ("jpg", "jpeg") and content.startswith(b"\xff\xd8\xff"))
            require(valid, "仅支持内容匹配的 PDF、PNG、JPG 文件")
            if suffix == "pdf":
                require(not any(token in content.lower() for token in (b"/javascript", b"/launch", b"/embeddedfile", b"/openaction", b"/richmedia")), "不支持包含脚本、嵌入文件或自动动作的 PDF")
            require(len(self.attachments(request_id)) < 10, "每张申请最多上传 10 份材料")
            mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}[suffix]
            self.db.execute("INSERT INTO leave_attachments VALUES(?,?,?,?,?,?,?,?)", str(uuid4()), request_id, user["id"], filename, mime, len(content), content, now_iso())
            self.touch(leave)
            self.action(leave, user, "upload_material", "上传材料：" + filename)
            return self.db.leave(request_id)

    def notifications(self, user):
        return {"items": [camel_row(r) for r in self.db.all("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200", user["id"])],
                "unread": self.db.one("SELECT COUNT(*) AS count FROM notifications WHERE user_id=? AND read_at IS NULL", user["id"])["count"]}

    def remove_attachment(self, key, user):
        with self.db.transaction():
            item = self.db.one("SELECT * FROM leave_attachments WHERE id=?", key)
            require(item, "材料不存在", 404)
            leave = self.leaves.require_leave(item["request_id"])
            require(leave["applicantId"] == user["id"], "只能移除自己的草稿材料", 403)
            require(leave["status"] == "draft" and not self.db.one(
                "SELECT id FROM approval_actions WHERE request_id=? AND action='submit'", leave["id"]),
                "已提交过的材料需保留记录，请上传补充材料并说明更正内容", 409)
            self.db.execute("DELETE FROM leave_attachments WHERE id=?", key)
            self.touch(leave)
            self.action(leave, user, "remove_material", "移除未提交材料：" + item["filename"])
            return self.db.leave(leave["id"])

    def remind(self, request_id, user):
        with self.db.transaction():
            leave = self.leaves.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能催办自己的申请", 403)
            require(leave["status"] == "human_reviewing", "当前没有可催办的请假审批")
            hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
            key = f"remind:{request_id}:{hour}"
            require(not self.db.one("SELECT id FROM notifications WHERE event_key=?", key), "本小时已催办，请稍后再试", 429)
            self.db.notify(leave["currentApproverId"], request_id, "申请人提醒你处理请假", f"{user['name']}希望你尽快处理。", key)
            self.action(leave, user, "remind", "申请人催办")

    def maintenance(self, recover=False):
        with self.db.transaction():
            if recover:
                for row in self.db.all("SELECT id,submission_manager_id FROM leave_requests WHERE status IN ('submitted','validating','agent_reviewing') AND deleted_at IS NULL"):
                    leave = self.db.leave(row["id"])
                    if leave["status"] == "submitted":
                        self.db.transition(row["id"], "validating", "system", "recovery", "系统", "recover", "恢复中断的申请")
                    manager = self.db.user(row["submission_manager_id"])
                    if manager and manager["status"] == "active" and manager["permissions"]["approveLeave"]:
                        self.leaves.route(row["id"], manager, "system", "recovery", "系统", "recover", "服务重启，申请已转人工继续处理")
                    else:
                        self.db.transition(row["id"], "need_information", "system", "recovery", "系统", "recover", "原审批人不可用，请联系 HR 调整后重新提交")
                        self.db.notify(leave["applicantId"], row["id"], "申请需要重新提交", "原审批人不可用，请联系 HR 调整后重新提交。")
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
            today = date.today().isoformat()
            for row in self.db.all("""SELECT lr.id,lr.current_approver_id AS approver_id,s.id AS task_id FROM leave_requests lr
                JOIN approval_steps s ON s.request_id=lr.id AND s.status='pending'
                WHERE lr.status='human_reviewing' AND s.created_at<?""", cutoff):
                self.db.notify(row["approver_id"], row["id"], "请假审批已等待超过 24 小时", "请及时处理，或转交给其他审批人。", f"overdue:{row['task_id']}:{today}")
            for row in self.db.all("SELECT * FROM leave_cancellations WHERE status='pending' AND created_at<?", cutoff):
                self.db.notify(row["approver_id"], row["request_id"], "销假审批已等待超过 24 小时", "请及时处理销假申请。", f"cancel-overdue:{row['id']}:{today}")
