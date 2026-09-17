import asyncio
import json
from datetime import datetime, timedelta, timezone

from .domain import now_iso, parse_time, require, utc_iso
from .calendar import segments, hours
from .agents import deterministic_review
from .organization import OrganizationService


class LeaveService:
    def __init__(self, db, reviewer):
        self.db, self.reviewer = db, reviewer

    def require_leave(self, request_id):
        value = self.db.leave(request_id, include_deleted=False)
        require(value, "请假申请不存在")
        return value

    def create(self, user, data):
        start, end = parse_time(data["startAt"]), parse_time(data["endAt"])
        require(end > start, "结束时间必须晚于开始时间")
        return self.db.create_leave(user, data)

    def route(self, request_id, manager, actor_type, actor_id, actor_name, action, reason):
        with self.db.transaction():
            value = self.db.transition(
                request_id, "human_reviewing", actor_type, actor_id, actor_name, action, reason, manager["id"]
            )
            self.db.ensure_step(request_id, manager)
            self.db.notify(manager["id"], request_id, "有新的请假待审批", f"{value['applicantName']}提交了请假申请，请查看材料及审核建议。")
            return value

    def check_version(self, leave, version):
        require(version is None or leave["version"] == version, "申请已更新，请刷新后再操作", 409)

    def edit(self, request_id, user, data):
        with self.db.transaction():
            leave = self.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能修改自己的请假申请")
            self.check_version(leave, data["version"])
            require(leave["status"] in ("draft", "need_information", "rejected", "withdrawn"), "请先撤回申请再修改")
            work = segments(self.db, data["startAt"], data["endAt"])
            if leave["status"] != "draft":
                self.db.transition(request_id, "draft", "human", user["id"], user["name"], "reopen", "修改后重新发起")
            fields = ["leaveType", "startAt", "endAt", "reason", "handoverUser", "handoverNotes"]
            changes = {key: {"before": leave.get(key), "after": data.get(key)} for key in fields if leave.get(key) != data.get(key)}
            self.db.execute("""UPDATE leave_requests SET leave_type=?,start_at=?,end_at=?,duration_hours=?,reason=?,
                handover_user=?,handover_notes=?,work_segments=?,agent_decision=NULL,agent_reason=NULL,
                agent_confidence=NULL,version=version+1,updated_at=? WHERE id=?""",
                data["leaveType"], utc_iso(data["startAt"]), utc_iso(data["endAt"]), hours(work), data["reason"],
                data.get("handoverUser"), data.get("handoverNotes"), json.dumps(work), now_iso(), request_id)
            self.db.record_action(request_id, "human", user["id"], user["name"], "edit",
                                  json.dumps(changes, ensure_ascii=False), "draft", "draft")
            return self.db.leave(request_id)

    def withdraw(self, request_id, user, reason, version):
        with self.db.transaction():
            leave = self.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能撤回自己的请假申请")
            self.check_version(leave, version)
            require(leave["status"] in ("submitted", "validating", "agent_reviewing", "human_reviewing", "need_information"), "当前状态不能撤回")
            value = self.db.transition(request_id, "withdrawn", "human", user["id"], user["name"], "withdraw", reason)
            self.db.execute("UPDATE approval_steps SET status='skipped',comment=?,reviewed_at=? WHERE request_id=? AND status='pending'",
                            reason, now_iso(), request_id)
            if leave["currentApproverId"]:
                self.db.notify(leave["currentApproverId"], request_id, "请假申请已撤回", f"{user['name']}已撤回申请。")
            return value

    def validate_submission(self, leave, user, version=None):
        require(leave["applicantId"] == user["id"], "只能提交自己的请假申请")
        self.check_version(leave, version)
        require(leave["status"] == "draft", "只有草稿可以提交")
        require(segments(self.db, leave["startAt"], leave["endAt"]) == leave["workSegments"],
                "工作日历已更新，请编辑并保存草稿，确认新时长后再提交", 409)
        require(leave["durationHours"] > 0, "请假时间没有覆盖公司工作时段")
        require(parse_time(leave["startAt"]) >= datetime.now(timezone.utc) - timedelta(minutes=1),
                "不能提交已经开始的请假")
        applicant = self.db.user(user["id"])
        manager = OrganizationService(self.db).require_approver(applicant)
        return applicant, manager

    def start_submission(self, request_id, user, version=None):
        """Commit submission before asynchronous review; safe to compose with creation."""
        with self.db.transaction():
            leave = self.require_leave(request_id)
            applicant, manager = self.validate_submission(leave, user, version)
            sufficient = self.db.remaining(user["id"], leave["leaveType"]) >= leave["durationHours"]
            overlap = self.db.overlap(leave)
            self.db.execute(
                "UPDATE leave_requests SET department_snapshot=?,submission_manager_id=? WHERE id=? AND status='draft'",
                applicant["department"],
                manager["id"],
                request_id,
            )
            self.db.transition(
                request_id,
                "submitted",
                "human",
                user["id"],
                user["name"],
                "submit",
                f"申请人确认提交，所属组织：{applicant['department']}，请假审批人：{manager['name']}",
            )
            self.db.transition(
                request_id, "validating", "system", "leave-workflow-v1", "请假流程", "validate", "完成确定性规则校验"
            )
            if not sufficient or overlap:
                current = self.route(
                    request_id,
                    manager,
                    "system",
                    "leave-workflow-v1",
                    "请假流程",
                    "route_to_human",
                    "余额不足，转人工处理" if not sufficient else "存在时间冲突，转人工处理",
                )
                return current, manager, sufficient, overlap
            current = self.db.transition(
                request_id,
                "agent_reviewing",
                "system",
                "leave-workflow-v1",
                "请假流程",
                "start_agent_review",
                "进入智能审核节点",
            )
            return current, manager, sufficient, overlap

    async def submit(self, request_id, user, version=None):
        return await self.review_submission(*self.start_submission(request_id, user, version))

    async def review_submission(self, submitted, manager, sufficient, overlap):
        # No database transaction is held while awaiting the model.
        if submitted["status"] != "agent_reviewing":
            return submitted
        request_id = submitted["id"]
        try:
            review = await asyncio.wait_for(self.reviewer(submitted, sufficient, overlap), timeout=50)
        except Exception:
            review = deterministic_review(submitted, sufficient, overlap)
            review["reason"] = "智能审核暂不可用，请人工核验材料和制度。"
        with self.db.transaction():
            current = self.db.leave(request_id)
            if current["status"] != "agent_reviewing" or current["version"] != submitted["version"]:
                return current
            self.db.save_review(request_id, review)
            sufficient = self.db.remaining(current["applicantId"], current["leaveType"]) >= current["durationHours"]
            overlap = self.db.overlap(current)
            actor = ("agent", "leave-review-agent-v1", "请假审核智能体")
            return self.route(request_id, manager, *actor, "escalate", "AI 辅助建议：" + review["reason"])

    def decide(self, request_id, actor, decision, reason, version=None):
        with self.db.transaction():
            current = self.require_leave(request_id)
            self.check_version(current, version)
            require(current["status"] == "human_reviewing", "该申请不在人工审批状态")
            require(current["currentApproverId"] == actor["id"], "当前用户不是该申请的审批人")
            user = self.db.user(actor["id"])
            require(
                user and user["status"] == "active" and user["permissions"]["approveLeave"], "当前账号没有请假审批权限"
            )
            require(current["applicantId"] != actor["id"], "不能审批自己的请假申请")
            if decision in ("reject", "request_information"):
                require(len(reason.strip()) >= 2, "驳回或退回补充时必须填写原因")
            if decision == "approve":
                # Other pending requests are resolved by returning one for correction first.
                require(not self.db.overlap(current), "与其他有效申请时间冲突，请先退回修改或撤回重复申请", 409)
            reason = reason.strip() or "同意"
            status = {"approve": "approved", "reject": "rejected", "request_information": "need_information"}[decision]
            result = self.db.transition(request_id, status, "human", actor["id"], actor["name"], decision, reason)
            self.db.complete_step(request_id, actor["id"], "skipped" if status == "need_information" else status, reason)
            if decision == "approve":
                self.db.deduct_balance(result)
            title = {"approved": "请假已批准", "rejected": "请假已驳回", "need_information": "请假需要补充材料"}[status]
            self.db.notify(current["applicantId"], request_id, title, "请打开申请查看审批意见。")
            return result

    def delete(self, request_id, user):
        with self.db.transaction():
            leave = self.require_leave(request_id)
            require(leave["applicantId"] == user["id"], "只能删除自己的请假申请")
            require(leave["status"] != "approved", "已批准的请假不能直接删除，请先走销假流程")
            if leave["status"] in ("submitted", "validating", "agent_reviewing", "human_reviewing", "need_information"):
                leave = self.db.transition(
                    request_id, "withdrawn", "human", user["id"], user["name"], "withdraw", "删除前自动撤回申请"
                )
                self.db.execute(
                    "UPDATE approval_steps SET status='skipped',comment='申请已撤回',reviewed_at=? WHERE request_id=? AND status='pending'",
                    now_iso(),
                    request_id,
                )
            changed = self.db.execute(
                "UPDATE leave_requests SET deleted_at=?,version=version+1,updated_at=? WHERE id=? AND version=? AND deleted_at IS NULL",
                now_iso(),
                now_iso(),
                request_id,
                leave["version"],
            )
            require(changed.rowcount == 1, "申请已删除或被其他操作更新", 409)
            self.db.record_action(
                request_id,
                "human",
                user["id"],
                user["name"],
                "delete",
                "申请人删除申请",
                leave["status"],
                leave["status"],
            )
