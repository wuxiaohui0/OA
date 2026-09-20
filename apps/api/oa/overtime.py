"""Overtime and compensatory-time approval workflows."""

from datetime import date
from uuid import uuid4

from .db import camel_row
from .domain import BusinessError, now_iso, parse_time, require


class OvertimeService:
    PENDING = "human_reviewing"

    def __init__(self, db, organization):
        self.db = db
        self.organization = organization

    def _require_active(self, user):
        require(user["status"] == "active", "账号已停用")

    def _approver(self, user):
        return self.organization.require_approver(user)

    @staticmethod
    def _hours(start_at, end_at):
        try:
            start, end = parse_time(start_at), parse_time(end_at)
        except Exception:
            raise BusinessError("加班时间格式不正确") from None
        require(end > start, "加班结束时间必须晚于开始时间")
        hours = round((end - start).total_seconds() / 3600, 2)
        require(hours > 0 and hours <= 24, "单次加班时长须在 24 小时以内")
        return hours

    @property
    def overtime_select(self):
        return """SELECT ot.*, applicant.name AS applicant_name, applicant.department AS department,
            approver.name AS current_approver_name
            FROM overtime_requests ot JOIN users applicant ON applicant.id=ot.applicant_id
            LEFT JOIN users approver ON approver.id=ot.current_approver_id"""

    @property
    def comp_select(self):
        return """SELECT ct.*, applicant.name AS applicant_name, applicant.department AS department,
            approver.name AS current_approver_name
            FROM comp_time_requests ct JOIN users applicant ON applicant.id=ct.applicant_id
            LEFT JOIN users approver ON approver.id=ct.current_approver_id"""

    def _map(self, row):
        return camel_row(row) if row else None

    def get_overtime(self, request_id, include_deleted=True):
        return self._map(self.db.one(self.overtime_select + " WHERE ot.id=? AND (? OR ot.deleted_at IS NULL)", request_id, include_deleted))

    def get_comp_time(self, request_id, include_deleted=True):
        return self._map(self.db.one(self.comp_select + " WHERE ct.id=? AND (? OR ct.deleted_at IS NULL)", request_id, include_deleted))

    def create_overtime(self, user, data):
        self._require_active(user)
        hours = self._hours(data["startAt"], data["endAt"])
        request_id, now = str(uuid4()), now_iso()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO overtime_requests
                (id,applicant_id,start_at,end_at,hours,reason,compensation_type,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'draft',?,?)""",
                request_id, user["id"], data["startAt"], data["endAt"], hours, data["reason"], data["compensationType"], now, now,
            )
            self._action("overtime", request_id, user, "create", "创建加班申请草稿", None, "draft")
        return self.get_overtime(request_id)

    def create_comp_time(self, user, data):
        self._require_active(user)
        try:
            parsed = date.fromisoformat(data["date"][:10])
            require(parsed.isoformat() == data["date"][:10], "调休日期格式不正确")
        except (ValueError, TypeError, AttributeError):
            raise BusinessError("调休日期格式不正确") from None
        request_id, now = str(uuid4()), now_iso()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO comp_time_requests
                (id,applicant_id,date,hours,reason,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'draft',?,?)""",
                request_id, user["id"], data["date"], data["hours"], data["reason"], now, now,
            )
            self._action("comp", request_id, user, "create", "创建调休申请草稿", None, "draft")
        return self.get_comp_time(request_id)

    def _transition_submit(self, kind, request_id, user, version=None):
        get = self.get_overtime if kind == "overtime" else self.get_comp_time
        table = "overtime_requests" if kind == "overtime" else "comp_time_requests"
        request = get(request_id)
        label = "加班" if kind == "overtime" else "调休"
        require(request, f"{label}申请不存在", 404)
        require(request["applicantId"] == user["id"], f"只能提交自己的{label}申请", 403)
        require(request["status"] in ("draft", "rejected", "withdrawn"), "当前状态不能提交")
        if version is not None:
            require(request["version"] == version, "申请已被更新，请刷新后重试", 409)
        approver = self._approver(user)
        with self.db.transaction():
            updated = self.db.execute(
                f"""UPDATE {table} SET status=?,current_approver_id=?,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                self.PENDING, approver["id"], now_iso(), request_id, request["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(kind, request_id, user, "submit", f"提交{label}申请", request["status"], self.PENDING)
        return get(request_id)

    def submit_overtime(self, request_id, user, version=None):
        return self._transition_submit("overtime", request_id, user, version)

    def submit_comp_time(self, request_id, user, version=None):
        return self._transition_submit("comp", request_id, user, version)

    def _decide(self, kind, request_id, user, decision, reason="", version=None):
        require(decision in ("approve", "reject"), "不支持的审批操作")
        if decision == "reject":
            label = "加班" if kind == "overtime" else "调休"
            require(reason.strip(), f"驳回{label}申请必须填写原因")
        get = self.get_overtime if kind == "overtime" else self.get_comp_time
        table = "overtime_requests" if kind == "overtime" else "comp_time_requests"
        request = get(request_id)
        label = "加班" if kind == "overtime" else "调休"
        require(request, f"{label}申请不存在", 404)
        require(request["status"] == self.PENDING, "当前状态不能审批")
        require(request["currentApproverId"] == user["id"], "当前用户不是该申请的审批人", 403)
        if version is not None:
            require(request["version"] == version, "申请已被更新，请刷新后重试", 409)
        with self.db.transaction():
            if decision == "approve" and kind == "comp":
                balance = self.comp_balance(request["applicantId"])
                require(balance + 1e-9 >= request["hours"], f"调休余额不足，当前可用 {balance:.2f} 小时")
            status = "approved" if decision == "approve" else "rejected"
            updated = self.db.execute(
                f"""UPDATE {table} SET status=?,current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND status=?""",
                status, now_iso(), request_id, request["version"], self.PENDING,
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(kind, request_id, user, decision, reason or ("审批通过" if decision == "approve" else f"驳回{label}申请"), request["status"], status)
            if decision == "approve" and kind == "overtime" and request["compensationType"] == "comp_leave":
                self.db.execute(
                    """INSERT OR IGNORE INTO comp_time_ledger(entry_key,user_id,overtime_request_id,delta_hours,reason,actor_id,actor_name,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    f"overtime:{request_id}", request["applicantId"], request_id, request["hours"], "加班审批通过，增加调休余额", user["id"], user["name"], now_iso(),
                )
            if decision == "approve" and kind == "comp":
                self.db.execute(
                    """INSERT OR IGNORE INTO comp_time_ledger(entry_key,user_id,comp_time_request_id,delta_hours,reason,actor_id,actor_name,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    f"comp:{request_id}", request["applicantId"], request_id, -request["hours"], "调休审批通过，扣减调休余额", user["id"], user["name"], now_iso(),
                )
        return get(request_id)

    def decide_overtime(self, request_id, user, decision, reason="", version=None):
        return self._decide("overtime", request_id, user, decision, reason, version)

    def decide_comp_time(self, request_id, user, decision, reason="", version=None):
        return self._decide("comp", request_id, user, decision, reason, version)

    def _withdraw(self, kind, request_id, user, reason="申请人撤回"):
        get = self.get_overtime if kind == "overtime" else self.get_comp_time
        table = "overtime_requests" if kind == "overtime" else "comp_time_requests"
        label = "加班" if kind == "overtime" else "调休"
        request = get(request_id)
        require(request, f"{label}申请不存在", 404)
        require(request["applicantId"] == user["id"], f"只能撤回自己的{label}申请", 403)
        require(request["status"] in ("draft", self.PENDING), "当前状态不能撤回")
        with self.db.transaction():
            updated = self.db.execute(
                f"""UPDATE {table} SET status='withdrawn',current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""", now_iso(), request_id, request["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(kind, request_id, user, "withdraw", reason, request["status"], "withdrawn")
        return get(request_id)

    def withdraw_overtime(self, request_id, user, reason="申请人撤回"):
        return self._withdraw("overtime", request_id, user, reason)

    def withdraw_comp_time(self, request_id, user, reason="申请人撤回"):
        return self._withdraw("comp", request_id, user, reason)

    def _action(self, kind, request_id, user, action, reason, before, after):
        table = "overtime_actions" if kind == "overtime" else "comp_time_actions"
        self.db.execute(
            f"INSERT INTO {table}(request_id,actor_id,actor_name,action,reason,from_status,to_status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            request_id, user["id"], user["name"], action, reason, before, after, now_iso(),
        )

    def details_overtime(self, request_id, user):
        request = self.get_overtime(request_id)
        require(request and (request["applicantId"] == user["id"] or request["currentApproverId"] == user["id"] or user["permissions"]["manageOrganization"]), "加班申请不存在", 404)
        actions = [camel_row(row) for row in self.db.all("SELECT * FROM overtime_actions WHERE request_id=? ORDER BY id", request_id)]
        return {"overtime": request, "actions": actions}

    def details_comp_time(self, request_id, user):
        request = self.get_comp_time(request_id)
        require(request and (request["applicantId"] == user["id"] or request["currentApproverId"] == user["id"] or user["permissions"]["manageOrganization"]), "调休申请不存在", 404)
        actions = [camel_row(row) for row in self.db.all("SELECT * FROM comp_time_actions WHERE request_id=? ORDER BY id", request_id)]
        return {"compTime": request, "actions": actions}

    def comp_balance(self, user_id):
        row = self.db.one("SELECT COALESCE(SUM(delta_hours),0) AS balance FROM comp_time_ledger WHERE user_id=?", user_id)
        return round(float(row["balance"] if row else 0), 2)

    def list(self, user):
        overtime_mine = [self._map(row) for row in self.db.all(self.overtime_select + " WHERE ot.applicant_id=? AND ot.deleted_at IS NULL ORDER BY ot.updated_at DESC", user["id"])]
        overtime_inbox = [self._map(row) for row in self.db.all(self.overtime_select + " WHERE ot.current_approver_id=? AND ot.status=? AND ot.deleted_at IS NULL ORDER BY ot.updated_at DESC", user["id"], self.PENDING)]
        overtime_history = [self._map(row) for row in self.db.all(self.overtime_select + " JOIN (SELECT request_id,MAX(id) AS action_id FROM overtime_actions WHERE actor_id=? AND action IN ('approve','reject') GROUP BY request_id) last_action ON last_action.request_id=ot.id JOIN overtime_actions oa ON oa.id=last_action.action_id WHERE ot.deleted_at IS NULL ORDER BY oa.created_at DESC", user["id"])]
        comp_mine = [self._map(row) for row in self.db.all(self.comp_select + " WHERE ct.applicant_id=? AND ct.deleted_at IS NULL ORDER BY ct.updated_at DESC", user["id"])]
        comp_inbox = [self._map(row) for row in self.db.all(self.comp_select + " WHERE ct.current_approver_id=? AND ct.status=? AND ct.deleted_at IS NULL ORDER BY ct.updated_at DESC", user["id"], self.PENDING)]
        comp_history = [self._map(row) for row in self.db.all(self.comp_select + " JOIN (SELECT request_id,MAX(id) AS action_id FROM comp_time_actions WHERE actor_id=? AND action IN ('approve','reject') GROUP BY request_id) last_action ON last_action.request_id=ct.id JOIN comp_time_actions ca ON ca.id=last_action.action_id WHERE ct.deleted_at IS NULL ORDER BY ca.created_at DESC", user["id"])]
        return {
            "overtime": {"mine": overtime_mine, "inbox": overtime_inbox, "history": overtime_history},
            "compTime": {"mine": comp_mine, "inbox": comp_inbox, "history": comp_history},
            "balance": self.comp_balance(user["id"]),
        }
