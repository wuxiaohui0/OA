"""Business trip request workflow with an auditable approval trail."""

import json
from uuid import uuid4

from .db import camel_row
from .domain import parse_time, require, now_iso


class TravelService:
    PENDING = "human_reviewing"
    TRANSPORT = {"economy", "high_speed", "business"}
    ACCOMMODATION = {"none", "standard", "premium"}

    def __init__(self, db, organization):
        self.db = db
        self.organization = organization

    @property
    def select(self):
        return """SELECT tr.*, applicant.name AS applicant_name, applicant.department AS department,
            approver.name AS current_approver_name
            FROM travel_requests tr JOIN users applicant ON applicant.id=tr.applicant_id
            LEFT JOIN users approver ON approver.id=tr.current_approver_id"""

    def _map(self, row):
        if not row:
            return None
        item = camel_row(row)
        item["travelerIds"] = json.loads(item.get("travelerIds") or "[]")
        names = []
        for traveler_id in item["travelerIds"]:
            user = self.db.user(traveler_id)
            if user:
                names.append(user["name"])
        item["travelerNames"] = names
        return item

    def get(self, request_id, include_deleted=True):
        row = self.db.one(
            self.select + " WHERE tr.id=? AND (? OR tr.deleted_at IS NULL)", request_id, include_deleted
        )
        return self._map(row)

    def _require_active(self, user):
        require(user["status"] == "active", "账号已停用")

    def _validate_dates(self, data):
        try:
            require(parse_time(data["endAt"]) > parse_time(data["startAt"]), "返程时间必须晚于出发时间")
        except TypeError:
            raise

    def _validate_travelers(self, user, traveler_ids):
        unique = list(dict.fromkeys(traveler_ids or []))
        if user["id"] not in unique:
            unique.insert(0, user["id"])
        require(len(unique) <= 20, "同行人不能超过 20 人")
        for traveler_id in unique:
            traveler = self.db.user(traveler_id)
            require(traveler and traveler["status"] == "active", "同行人不存在或账号已停用")
        return unique

    def _approver(self, user):
        return self.organization.require_approver(user)

    def create(self, user, data):
        self._require_active(user)
        self._validate_dates(data)
        traveler_ids = self._validate_travelers(user, data.get("travelerIds", []))
        request_id, now = str(uuid4()), now_iso()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO travel_requests
                (id,applicant_id,destination,purpose,start_at,end_at,traveler_ids,transport_standard,
                 accommodation_standard,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                request_id,
                user["id"],
                data["destination"],
                data["purpose"],
                data["startAt"],
                data["endAt"],
                json.dumps(traveler_ids, ensure_ascii=False),
                data["transportStandard"],
                data["accommodationStandard"],
                now,
                now,
            )
            self._action(request_id, user, "create", "创建出差申请草稿", None, "draft")
        return self.get(request_id)

    def submit(self, request_id, user, version=None):
        with self.db.transaction():
            trip = self.get(request_id)
            require(trip, "出差申请不存在", 404)
            require(trip["applicantId"] == user["id"], "只能提交自己的出差申请", 403)
            require(trip["status"] in ("draft", "rejected", "withdrawn"), "当前状态不能提交")
            if version is not None:
                require(trip["version"] == version, "申请已被更新，请刷新后重试", 409)
            approver = self._approver(user)
            updated = self.db.execute(
                """UPDATE travel_requests SET status=?,current_approver_id=?,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                self.PENDING,
                approver["id"],
                now_iso(),
                request_id,
                trip["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "submit", "提交出差申请", trip["status"], self.PENDING)
        return self.get(request_id)

    def decide(self, request_id, user, decision, reason="", version=None):
        require(decision in ("approve", "reject"), "不支持的审批操作")
        if decision == "reject":
            require(reason.strip(), "驳回出差申请必须填写原因")
        with self.db.transaction():
            trip = self.get(request_id)
            require(trip, "出差申请不存在", 404)
            require(trip["status"] == self.PENDING, "当前状态不能审批")
            require(trip["currentApproverId"] == user["id"], "当前用户不是该申请的审批人", 403)
            if version is not None:
                require(trip["version"] == version, "申请已被更新，请刷新后重试", 409)
            status = "approved" if decision == "approve" else "rejected"
            updated = self.db.execute(
                """UPDATE travel_requests SET status=?,current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND status=?""",
                status,
                now_iso(),
                request_id,
                trip["version"],
                self.PENDING,
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, decision, reason or ("审批通过" if decision == "approve" else "驳回出差申请"), trip["status"], status)
        return self.get(request_id)

    def withdraw(self, request_id, user, reason="申请人撤回"):
        with self.db.transaction():
            trip = self.get(request_id)
            require(trip, "出差申请不存在", 404)
            require(trip["applicantId"] == user["id"], "只能撤回自己的出差申请", 403)
            require(trip["status"] in ("draft", self.PENDING), "当前状态不能撤回")
            updated = self.db.execute(
                """UPDATE travel_requests SET status='withdrawn',current_approver_id=NULL,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                now_iso(), request_id, trip["version"]
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self._action(request_id, user, "withdraw", reason, trip["status"], "withdrawn")
        return self.get(request_id)

    def _action(self, request_id, user, action, reason, before, after):
        self.db.execute(
            """INSERT INTO travel_actions(request_id,actor_id,actor_name,action,reason,from_status,to_status,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            request_id, user["id"], user["name"], action, reason, before, after, now_iso()
        )

    def details(self, request_id, user):
        trip = self.get(request_id)
        require(trip and (trip["applicantId"] == user["id"] or trip["currentApproverId"] == user["id"] or user["permissions"]["manageOrganization"]), "出差申请不存在", 404)
        actions = [camel_row(row) for row in self.db.all("SELECT * FROM travel_actions WHERE request_id=? ORDER BY id", request_id)]
        return {"travel": trip, "actions": actions}

    def list(self, user):
        mine = [self._map(row) for row in self.db.all(self.select + " WHERE tr.applicant_id=? AND tr.deleted_at IS NULL ORDER BY tr.updated_at DESC", user["id"])]
        inbox = [self._map(row) for row in self.db.all(self.select + " WHERE tr.current_approver_id=? AND tr.status=? AND tr.deleted_at IS NULL ORDER BY tr.updated_at DESC", user["id"], self.PENDING)]
        history = [self._map(row) for row in self.db.all(
            self.select + " JOIN (SELECT request_id,MAX(id) AS action_id FROM travel_actions WHERE actor_id=? AND action IN ('approve','reject') GROUP BY request_id) last_action ON last_action.request_id=tr.id JOIN travel_actions ta ON ta.id=last_action.action_id WHERE tr.deleted_at IS NULL ORDER BY ta.created_at DESC",
            user["id"],
        )]
        return {"mine": mine, "inbox": inbox, "history": history}
