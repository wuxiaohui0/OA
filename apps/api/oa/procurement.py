"""Purchase request workflow with a single, auditable approval step."""

import json
from uuid import uuid4

from .db import camel_row
from .domain import now_iso, require


class ProcurementService:
    PENDING = "human_reviewing"

    def __init__(self, db, organization):
        self.db = db
        self.organization = organization

    @property
    def select(self):
        return """SELECT pr.*, applicant.name AS applicant_name, applicant.department AS department,
            approver.name AS current_approver_name
            FROM procurement_requests pr JOIN users applicant ON applicant.id=pr.applicant_id
            LEFT JOIN users approver ON approver.id=pr.current_approver_id"""

    def _map(self, row):
        if not row:
            return None
        item = camel_row(row)
        item["items"] = json.loads(item.pop("itemsJson", "[]") or "[]")
        return item

    def get(self, request_id, include_deleted=True):
        row = self.db.one(
            self.select + " WHERE pr.id=? AND (? OR pr.deleted_at IS NULL)", request_id, include_deleted
        )
        return self._map(row)

    def _require_active(self, user):
        require(user["status"] == "active", "账号已停用")

    def create(self, user, data):
        self._require_active(user)
        items = data.get("items") or []
        estimated = round(sum(float(item["quantity"]) * float(item["unitPrice"]) for item in items), 2)
        require(estimated > 0, "采购明细金额必须大于 0")
        require(estimated <= float(data["budget"]), "预计采购金额不能超过预算")
        request_id, now = str(uuid4()), now_iso()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO procurement_requests
                (id,applicant_id,title,purpose,items_json,budget,estimated_amount,currency,supplier,need_by,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                request_id, user["id"], data["title"], data["purpose"], json.dumps(items, ensure_ascii=False),
                data["budget"], estimated, data["currency"], data.get("supplier"), data["needBy"], now, now,
            )
            self._action(request_id, user, "create", "创建采购申请草稿", None, "draft")
        return self.get(request_id)

    def submit(self, request_id, user, version=None):
        with self.db.transaction():
            request = self.get(request_id)
            require(request, "采购申请不存在", 404)
            require(request["applicantId"] == user["id"], "只能提交自己的采购申请", 403)
            require(request["status"] in ("draft", "rejected", "withdrawn"), "当前状态不能提交")
            if version is not None:
                require(request["version"] == version, "申请已被更新，请刷新后重试", 409)
            approver = self.organization.require_approver(user)
            updated = self.db.execute(
                """UPDATE procurement_requests SET status=?,current_approver_id=?,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                self.PENDING, approver["id"], now_iso(), request_id, request["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "submit", "提交采购申请", request["status"], self.PENDING)
        return self.get(request_id)

    def decide(self, request_id, user, decision, reason="", version=None):
        require(decision in ("approve", "reject"), "不支持的审批操作")
        if decision == "reject":
            require(reason.strip(), "驳回采购申请必须填写原因")
        with self.db.transaction():
            request = self.get(request_id)
            require(request, "采购申请不存在", 404)
            require(request["status"] == self.PENDING, "当前状态不能审批")
            require(request["currentApproverId"] == user["id"], "当前用户不是该申请的审批人", 403)
            if version is not None:
                require(request["version"] == version, "申请已被更新，请刷新后重试", 409)
            status = "approved" if decision == "approve" else "rejected"
            updated = self.db.execute(
                """UPDATE procurement_requests SET status=?,current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND status=?""",
                status, now_iso(), request_id, request["version"], self.PENDING,
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, decision, reason or ("审批通过" if decision == "approve" else "驳回采购申请"), request["status"], status)
        return self.get(request_id)

    def withdraw(self, request_id, user, reason="申请人撤回"):
        with self.db.transaction():
            request = self.get(request_id)
            require(request, "采购申请不存在", 404)
            require(request["applicantId"] == user["id"], "只能撤回自己的采购申请", 403)
            require(request["status"] in ("draft", self.PENDING), "当前状态不能撤回")
            updated = self.db.execute(
                """UPDATE procurement_requests SET status='withdrawn',current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                now_iso(), request_id, request["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "withdraw", reason, request["status"], "withdrawn")
        return self.get(request_id)

    def _action(self, request_id, user, action, reason, before, after):
        self.db.execute(
            """INSERT INTO procurement_actions(request_id,actor_id,actor_name,action,reason,from_status,to_status,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            request_id, user["id"], user["name"], action, reason, before, after, now_iso(),
        )

    def details(self, request_id, user):
        request = self.get(request_id)
        require(
            request and (request["applicantId"] == user["id"] or request["currentApproverId"] == user["id"] or user["permissions"]["manageOrganization"]),
            "采购申请不存在", 404,
        )
        actions = [camel_row(row) for row in self.db.all("SELECT * FROM procurement_actions WHERE request_id=? ORDER BY id", request_id)]
        return {"procurement": request, "actions": actions}

    def list(self, user):
        mine = [self._map(row) for row in self.db.all(self.select + " WHERE pr.applicant_id=? AND pr.deleted_at IS NULL ORDER BY pr.updated_at DESC", user["id"])]
        inbox = [self._map(row) for row in self.db.all(self.select + " WHERE pr.current_approver_id=? AND pr.status=? AND pr.deleted_at IS NULL ORDER BY pr.updated_at DESC", user["id"], self.PENDING)]
        history = [self._map(row) for row in self.db.all(
            self.select + " JOIN (SELECT request_id,MAX(id) AS action_id FROM procurement_actions WHERE actor_id=? AND action IN ('approve','reject') GROUP BY request_id) last_action ON last_action.request_id=pr.id JOIN procurement_actions pa ON pa.id=last_action.action_id WHERE pr.deleted_at IS NULL ORDER BY pa.created_at DESC",
            user["id"],
        )]
        return {"mine": mine, "inbox": inbox, "history": history}
