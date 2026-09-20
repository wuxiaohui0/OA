"""Expense reimbursement workflow.

This is intentionally a small, auditable workflow that shares the existing
organization and session model.  It can later be promoted to a generic form
engine for travel, purchase and overtime requests.
"""

from uuid import uuid4

from .db import camel_row
from .domain import require, now_iso


class ExpenseService:
    CATEGORIES = {"travel", "meal", "office", "software", "other"}
    PAYMENT_METHODS = {"personal", "corporate_card", "cash", "other"}
    PENDING = "human_reviewing"

    def __init__(self, db, organization):
        self.db = db
        self.organization = organization

    @property
    def select(self):
        return """SELECT er.*, applicant.name AS applicant_name, applicant.department AS department,
            tr.destination AS travel_destination,
            approver.name AS current_approver_name
            FROM expense_requests er JOIN users applicant ON applicant.id=er.applicant_id
            LEFT JOIN travel_requests tr ON tr.id=er.travel_request_id
            LEFT JOIN users approver ON approver.id=er.current_approver_id"""

    def _map(self, row):
        if not row:
            return None
        return camel_row(row)

    def get(self, request_id, include_deleted=True):
        row = self.db.one(
            self.select + " WHERE er.id=? AND (? OR er.deleted_at IS NULL)", request_id, include_deleted
        )
        return self._map(row)

    def _require_active(self, user):
        require(user["status"] == "active", "账号已停用")

    def _approver(self, user):
        return self.organization.require_approver(user)

    def create(self, user, data):
        self._require_active(user)
        travel_request_id = data.get("travelRequestId")
        if travel_request_id:
            travel = self.db.one("SELECT id,status,applicant_id FROM travel_requests WHERE id=? AND deleted_at IS NULL", travel_request_id)
            require(travel and travel["applicant_id"] == user["id"], "只能关联自己的出差申请")
            require(travel["status"] == "approved", "只能关联已通过的出差申请")
        request_id, now = str(uuid4()), now_iso()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO expense_requests
                (id,applicant_id,category,amount,currency,occurred_at,description,payment_method,travel_request_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                request_id,
                user["id"],
                data["category"],
                data["amount"],
                data["currency"],
                data["occurredAt"],
                data["description"],
                data["paymentMethod"],
                travel_request_id,
                now,
                now,
            )
            self._action(request_id, user, "create", "创建报销草稿", None, "draft")
        return self.get(request_id)

    def submit(self, request_id, user, version=None):
        with self.db.transaction():
            expense = self.get(request_id)
            require(expense, "报销申请不存在", 404)
            require(expense["applicantId"] == user["id"], "只能提交自己的报销申请", 403)
            require(expense["status"] in ("draft", "rejected", "withdrawn"), "当前状态不能提交")
            if version is not None:
                require(expense["version"] == version, "申请已被更新，请刷新后重试", 409)
            approver = self._approver(user)
            updated = self.db.execute(
                """UPDATE expense_requests SET status=?,current_approver_id=?,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                self.PENDING,
                approver["id"],
                now_iso(),
                request_id,
                expense["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "submit", "提交报销申请", expense["status"], self.PENDING)
        return self.get(request_id)

    def decide(self, request_id, user, decision, reason="", version=None):
        require(decision in ("approve", "reject"), "不支持的审批操作")
        if decision == "reject":
            require(reason.strip(), "驳回报销必须填写原因")
        with self.db.transaction():
            expense = self.get(request_id)
            require(expense, "报销申请不存在", 404)
            require(expense["status"] == self.PENDING, "当前状态不能审批")
            require(expense["currentApproverId"] == user["id"], "当前用户不是该申请的审批人", 403)
            if version is not None:
                require(expense["version"] == version, "申请已被更新，请刷新后重试", 409)
            status = "approved" if decision == "approve" else "rejected"
            updated = self.db.execute(
                """UPDATE expense_requests SET status=?,current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND status=?""",
                status,
                now_iso(),
                request_id,
                expense["version"],
                self.PENDING,
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, decision, reason or ("审批通过" if decision == "approve" else "驳回报销"), expense["status"], status)
        return self.get(request_id)

    def withdraw(self, request_id, user, reason="申请人撤回"):
        with self.db.transaction():
            expense = self.get(request_id)
            require(expense, "报销申请不存在", 404)
            require(expense["applicantId"] == user["id"], "只能撤回自己的报销申请", 403)
            require(expense["status"] in ("draft", self.PENDING), "当前状态不能撤回")
            updated = self.db.execute(
                """UPDATE expense_requests SET status='withdrawn',current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                now_iso(), request_id, expense["version"]
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "withdraw", reason, expense["status"], "withdrawn")
        return self.get(request_id)

    def _action(self, request_id, user, action, reason, before, after):
        self.db.execute(
            """INSERT INTO expense_actions(request_id,actor_id,actor_name,action,reason,from_status,to_status,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            request_id, user["id"], user["name"], action, reason, before, after, now_iso()
        )

    def details(self, request_id, user):
        expense = self.get(request_id)
        require(expense and (expense["applicantId"] == user["id"] or expense["currentApproverId"] == user["id"] or user["permissions"]["manageOrganization"]), "报销申请不存在", 404)
        actions = [camel_row(row) for row in self.db.all("SELECT * FROM expense_actions WHERE request_id=? ORDER BY id", request_id)]
        return {"expense": expense, "actions": actions}

    def list(self, user):
        mine = [self._map(row) for row in self.db.all(self.select + " WHERE er.applicant_id=? AND er.deleted_at IS NULL ORDER BY er.updated_at DESC", user["id"])]
        inbox = [self._map(row) for row in self.db.all(self.select + " WHERE er.current_approver_id=? AND er.status=? AND er.deleted_at IS NULL ORDER BY er.updated_at DESC", user["id"], self.PENDING)]
        history = [self._map(row) for row in self.db.all(
            self.select + " JOIN (SELECT request_id,MAX(id) AS action_id FROM expense_actions WHERE actor_id=? AND action IN ('approve','reject') GROUP BY request_id) last_action ON last_action.request_id=er.id JOIN expense_actions ea ON ea.id=last_action.action_id WHERE er.deleted_at IS NULL ORDER BY ea.created_at DESC",
            user["id"],
        )]
        return {"mine": mine, "inbox": inbox, "history": history}
