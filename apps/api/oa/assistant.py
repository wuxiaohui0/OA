"""Authenticated OA commands. Models can query and prepare, never confirm."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import Field, SecretStr, ValidationError

from .auth import hash_password
from .agent_profiles import resolve_agent_assignment
from .calendar import clip, hours
from .domain import (
    BalanceAdjustmentInput, BusinessError, CalendarDayInput, CancellationInput,
    DecisionInput, DepartmentInput, EmployeeInput, InputModel, LeaveEditInput,
    LeaveInput, MessageInput, OnboardInput, PositionInput, ReasonInput, RoleInput,
    SHANGHAI, TransferInput, VersionReasonInput, TravelInput, ProcurementInput,
    OvertimeInput, CompTimeInput, now_iso, parse_time, require,
)
from .run_control import AGENT_RUNS

LABELS = {
    "apply_leave": "申请并提交请假",
    "create_leave": "创建请假草稿", "edit_leave": "修改请假申请", "submit": "提交请假",
    "approve": "批准请假", "reject": "驳回请假", "request_information": "退回补充",
    "withdraw": "撤回请假", "delete_leave": "删除申请", "remind": "催办",
    "transfer": "交接审批", "cancel_leave": "申请销假",
    "approve_cancellation": "批准销假", "reject_cancellation": "驳回销假",
    "withdraw_cancellation": "撤回销假", "transfer_cancellation": "交接销假审批",
    "remove_attachment": "删除材料", "save_calendar": "设置工作日历",
    "reset_calendar": "恢复默认日历", "adjust_balance": "调整假期额度",
    "read_notifications": "标记全部通知已读", "update_employee": "修改员工",
    "onboard_employee": "员工入职", "save_department": "保存部门",
    "create_position": "新增岗位", "create_role": "新增权限角色",
    "create_travel": "创建出差申请草稿", "submit_travel": "提交出差申请", "withdraw_travel": "撤回出差申请",
    "create_procurement": "创建采购申请草稿", "submit_procurement": "提交采购申请", "withdraw_procurement": "撤回采购申请",
    "create_overtime": "创建加班申请草稿", "submit_overtime": "提交加班申请", "withdraw_overtime": "撤回加班申请",
    "create_comp_time": "创建调休申请草稿", "submit_comp_time": "提交调休申请", "withdraw_comp_time": "撤回调休申请",
}
SCHEMAS = {
    "apply_leave": LeaveInput, "create_leave": LeaveInput, "edit_leave": LeaveEditInput,
    "submit": DecisionInput, "approve": DecisionInput, "reject": DecisionInput,
    "request_information": VersionReasonInput, "withdraw": VersionReasonInput,
    "transfer": TransferInput, "cancel_leave": CancellationInput,
    "approve_cancellation": ReasonInput, "reject_cancellation": ReasonInput,
    "withdraw_cancellation": ReasonInput, "transfer_cancellation": TransferInput,
    "save_calendar": CalendarDayInput, "adjust_balance": BalanceAdjustmentInput,
    "update_employee": EmployeeInput, "onboard_employee": OnboardInput,
    "save_department": DepartmentInput, "create_position": PositionInput, "create_role": RoleInput,
    "create_travel": TravelInput, "submit_travel": DecisionInput, "withdraw_travel": DecisionInput,
    "create_procurement": ProcurementInput, "submit_procurement": DecisionInput, "withdraw_procurement": DecisionInput,
    "create_overtime": OvertimeInput, "submit_overtime": DecisionInput, "withdraw_overtime": DecisionInput,
    "create_comp_time": CompTimeInput, "submit_comp_time": DecisionInput, "withdraw_comp_time": DecisionInput,
}
LEAVE_OPS = {"edit_leave", "submit", "approve", "reject", "request_information", "withdraw",
             "delete_leave", "remind", "transfer", "cancel_leave"}
APPROVAL_OPS = {"approve", "reject", "request_information", "transfer", "approve_cancellation",
                "reject_cancellation", "transfer_cancellation"}
ADMIN_OPS = {"save_calendar", "reset_calendar", "adjust_balance", "update_employee", "onboard_employee",
             "save_department", "create_position", "create_role"}
TRAVEL_OPS = {"create_travel", "submit_travel", "withdraw_travel"}
PROCUREMENT_OPS = {"create_procurement", "submit_procurement", "withdraw_procurement"}
OVERTIME_OPS = {"create_overtime", "submit_overtime", "withdraw_overtime",
                "create_comp_time", "submit_comp_time", "withdraw_comp_time"}
BUSINESS_REQUEST_OPS = TRAVEL_OPS | PROCUREMENT_OPS | OVERTIME_OPS
BUSINESS_CREATE_OPS = {"create_travel", "create_procurement", "create_overtime", "create_comp_time"}
BUSINESS_SUBMIT_OPS = {"submit_travel", "submit_procurement", "submit_overtime", "submit_comp_time"}


def allowed_operations(user):
    return {op for op in LABELS
            if (op not in APPROVAL_OPS or user["permissions"]["approveLeave"])
            and (op not in ADMIN_OPS or user["permissions"]["manageOrganization"])}


STATE_LABELS = {"draft": "草稿", "human_reviewing": "待人工审批", "approved": "已批准",
                "rejected": "已驳回", "need_information": "待补充", "withdrawn": "已撤回",
                "cancelled": "已销假", "agent_reviewing": "辅助检查中"}
FIELD_LABELS = {
    "leaveType": "假别", "startAt": "开始时间", "endAt": "结束时间", "reason": "原因/意见",
    "handoverUser": "工作交接人", "handoverNotes": "交接说明", "approverId": "新审批人",
    "name": "名称", "employeeNo": "工号", "departmentId": "所属部门", "title": "岗位",
    "role": "权限角色", "managerId": "直属领导", "noManager": "无直属领导",
    "leaveApproverId": "请假审批人", "hiredAt": "入职日期", "status": "状态",
    "username": "登录账号", "leaveEntitlements": "初始假期额度（小时）",
    "parentId": "上级部门", "leaderId": "部门负责人", "permissions": "权限",
    "day": "日期", "isWorkday": "是否工作日", "userId": "员工", "hours": "额度变动（小时）",
    "destination": "出差地点", "purpose": "申请用途", "travelerIds": "同行人",
    "transportStandard": "交通标准", "accommodationStandard": "住宿标准",
    "items": "采购明细", "quantity": "数量", "unitPrice": "单价",
    "budget": "采购预算", "currency": "币种", "supplier": "供应商", "needBy": "需求日期",
    "compensationType": "补偿方式", "date": "调休日期",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ChatInput(MessageInput):
    context_id: str | None = None
    action_id: str | None = None


class PrepareInput(InputModel):
    conversation_id: str
    operation: str
    target_id: str | None = None
    data: dict = Field(default_factory=dict)


class ConfirmInput(InputModel):
    initial_password: SecretStr | None = Field(default=None, min_length=12, max_length=128)


class PreviewRollback(Exception):
    pass


class AssistantService:
    def __init__(self, db, leaves, pilot, organization, conversations, travel=None, procurement=None, overtime=None):
        self.db, self.leaves, self.pilot = db, leaves, pilot
        self.organization, self.conversations = organization, conversations
        self.travel, self.procurement, self.overtime = travel, procurement, overtime
        # Synchronous commands commit the business change and receipt in one transaction.
        # Submission tools await a reviewer. Recover receipts from the durable human submit event.
        for row in db.all("SELECT * FROM assistant_actions WHERE status='executing'"):
            data = json.loads(row["payload"])
            event = db.one("""SELECT id FROM approval_actions WHERE request_id=? AND actor_id=?
                AND action='submit' AND id>?""", row["target_id"], row["user_id"], data.get("_eventId", 0))
            result = {"message": "提交已受理；请查看申请最新状态。"} if event else {"message": "执行已中断，没有自动重试。请重新发起操作。"}
            if event:
                result["leave"] = db.leave(row["target_id"])
                try:
                    conversation = conversations.get(row["user_id"], row["conversation_id"])
                    if self.pending(conversation)["id"] == row["id"]:
                        conversation["contextId"] = row["target_id"]
                        conversations.save(conversation)
                except BusinessError:
                    pass  # An expired conversation does not prevent receipt recovery.
            db.execute("UPDATE assistant_actions SET status=?,result=?,updated_at=? WHERE id=?",
                       "succeeded" if event else "failed", encode(result), now_iso(), row["id"])

    def user(self, user):
        value = self.db.user(user["id"])
        require(value and value["status"] == "active", "账号已停用", 403)
        return value

    def agent_assignment(self, user):
        """Resolve capabilities from a fresh principal on every request.

        This prevents a long-lived conversation or model instance from retaining
        tools after an administrator changes the employee's role.
        """
        principal = self.user(user)
        return resolve_agent_assignment(principal, self.db, allowed_operations(principal))

    def action(self, action_id, user):
        row = self.db.one("SELECT * FROM assistant_actions WHERE id=? AND user_id=?", action_id, user["id"])
        require(row, "操作不存在", 404)
        return row

    @staticmethod
    def public(row):
        if not row:
            return None
        return {"id": row["id"], "operation": row["operation"], "targetId": row["target_id"],
                "preview": json.loads(row["preview"]), "status": row["status"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "editableData": {key: value for key, value in json.loads(row["payload"]).items()
                                 if key in ("leaveType", "startAt", "endAt", "reason", "handoverUser", "handoverNotes")}
                                if row["operation"] in ("apply_leave", "create_leave", "edit_leave") and row["status"] == "pending" else None,
                "createdAt": row["created_at"], "requiresPassword": row["operation"] == "onboard_employee"}

    def pending(self, conversation):
        return self.db.one("SELECT * FROM assistant_actions WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1", conversation["id"])

    def snapshot(self, conversation, user):
        context = None
        if conversation.get("contextId"):
            try:
                context = self.pilot.details(self.pilot.view(conversation["contextId"], user))
            except BusinessError:
                conversation["contextId"] = None
        cards = []
        for card in conversation.get("cards", []):
            source = card.get("source")
            if source:
                try:
                    card = self.query(user, **source)
                except BusinessError:
                    continue
            cards.append(card)
        return {**self.conversations.snapshot(conversation, None), "busy": conversation["busy"],
                "context": context, "action": self.public(self.pending(conversation)),
                "cards": cards, "clarification": conversation.get("clarification"),
                "agent": self.agent_assignment(user).public()}

    def query(self, user, resource, target_id=None, search="", scope="mine"):
        card = self.query_data(user, resource, target_id, search, scope)
        return {**card, "source": {"resource": resource, "target_id": target_id, "search": search, "scope": scope}}

    def query_data(self, user, resource, target_id=None, search="", scope="mine"):
        user = self.user(user)
        if resource == "requests":
            require(scope in ("mine", "pending", "history", "all"), "不支持的查询范围")
            if scope in ("pending", "history"):
                require(user["permissions"]["approveLeave"], "当前账号没有审批权限，可以查询自己的申请进度", 403)
            if scope == "all":
                self.organization.require_access(user)
                items = [self.db.map_leave(r) for r in self.db.all(self.db.LEAVE_SELECT + " WHERE lr.deleted_at IS NULL ORDER BY lr.updated_at DESC LIMIT 500")]
            else:
                items = self.db.list_leaves(user["id"])[{"mine": "mine", "pending": "inbox", "history": "approvalHistory"}[scope]]
            items = [x for x in items if search in encode(x) or search in STATE_LABELS.get(x["status"], "")]
            return {"kind": "requests", "title": {"mine": "我的申请", "pending": "待我审批", "history": "审批记录", "all": "请假台账"}[scope],
                    "items": items[:50], "total": len(items)}
        if resource == "request":
            return {"kind": "request", "title": "申请详情", **self.pilot.details(self.pilot.view(target_id, user))}
        if resource == "cancellations":
            require(user["permissions"]["approveLeave"], "当前账号没有审批权限，可以在自己的申请详情中查看销假进度", 403)
            return {"kind": "cancellations", "title": "销假待办", "items": self.pilot.cancellation_inbox(user)}
        if resource == "ledger":
            target_id = target_id or user["id"]
            return {"kind": "ledger", "title": "假期余额与流水", "employeeId": target_id,
                    **self.pilot.ledger(user, target_id)}
        if resource == "calendar":
            days = self.pilot.calendar()
            today = datetime.now(SHANGHAI).date()
            override = next((day for day in days if day["day"] == today.isoformat()), None)
            is_workday = bool(override["isWorkday"]) if override else today.weekday() < 5
            return {
                "kind": "calendar",
                "title": "工作日历",
                "timezone": "Asia/Shanghai",
                "defaultWorkdays": ["周一", "周二", "周三", "周四", "周五"],
                "defaultSessions": ["09:00-12:00", "13:30-18:00"],
                "today": {
                    "day": today.isoformat(),
                    "isWorkday": is_workday,
                    "source": "special-setting" if override else "default-week",
                },
                "days": days,
                "interpretation": "days 仅包含特殊日期设置；为空表示使用默认周一至周五，并不表示没有工作日。",
            }
        if resource == "notifications":
            return {"kind": "notifications", "title": "消息通知", **self.pilot.notifications(user)}
        if resource == "organization":
            employees = [u for u in self.db.users() if (u["status"] == "active" or user["permissions"]["manageOrganization"]) and search in encode(u)]
            return {"kind": "organization", "title": "组织与人员", "employees": employees,
                    "departments": self.db.departments(), "roles": self.db.roles(), "positions": self.db.positions()}
        if resource == "employee":
            employee = self.db.user(target_id)
            require(employee and (employee["status"] == "active" or user["permissions"]["manageOrganization"]), "员工不存在", 404)
            return {"kind": "employee", "title": "员工详情", "employee": employee,
                    "profile": self.organization.profile(employee)}
        raise BusinessError("不支持的查询")

    def target_leave(self, operation, target, user):
        if operation in LEAVE_OPS:
            return self.pilot.view(target, user)
        if operation.endswith("_cancellation"):
            row = self.db.one("SELECT request_id FROM leave_cancellations WHERE id=?", target)
            require(row, "销假申请不存在", 404)
            return self.pilot.view(row["request_id"], user)
        if operation == "remove_attachment":
            row = self.db.one("SELECT request_id FROM leave_attachments WHERE id=?", target)
            require(row, "材料不存在", 404)
            return self.pilot.view(row["request_id"], user)
        return None

    def target_business(self, operation, target, user):
        """Resolve a personal travel/procurement/overtime request for preview.

        The assistant is deliberately limited to the authenticated employee's
        own requests.  The domain service repeats this check at execution time;
        this check prevents an ID supplied in chat from leaking another
        employee's request into a confirmation card.
        """
        if operation not in BUSINESS_REQUEST_OPS:
            return None
        if operation in BUSINESS_CREATE_OPS:
            require(target is None, "新申请无需指定已有申请，请核对后重新发起")
            return None
        require(target, "请提供申请ID", 404)
        if operation in TRAVEL_OPS:
            service, label = self.travel, "出差申请"
            record = service.get(target) if service else None
        elif operation in PROCUREMENT_OPS:
            service, label = self.procurement, "采购申请"
            record = service.get(target) if service else None
        elif operation in {"create_overtime", "submit_overtime", "withdraw_overtime"}:
            service, label = self.overtime, "加班申请"
            record = service.get_overtime(target) if service else None
        else:
            service, label = self.overtime, "调休申请"
            record = service.get_comp_time(target) if service else None
        require(record, f"{label}不存在", 404)
        require(record["applicantId"] == user["id"], f"只能办理自己的{label}", 403)
        return record

    def target_record(self, operation, target, user):
        return self.target_leave(operation, target, user) or self.target_business(operation, target, user)

    def fingerprint(self, op, target, user):
        leave = self.target_leave(op, target, user)
        business = self.target_business(op, target, user)
        state = {"actor": self.user(user)}
        if leave:
            state.update(detail=self.pilot.details(leave), applicant=self.db.user(leave["applicantId"]),
                         balances=self.db.balances(leave["applicantId"]), calendar=self.pilot.calendar())
        elif business:
            state["request"] = business
        elif op in ("update_employee", "onboard_employee", "save_department", "create_role", "create_position"):
            state.update(users=self.db.users(), departments=self.db.departments(), roles=self.db.roles(), positions=self.db.positions())
        elif op == "apply_leave":
            state.update(calendar=self.pilot.calendar(), balances=self.db.balances(user["id"]),
                         approver=self.organization.require_approver(user),
                         department=self.organization.department(user["departmentId"]))
        elif op in ("create_leave", "save_calendar", "reset_calendar"):
            state["calendar"] = self.pilot.calendar()
        elif op == "adjust_balance":
            state["balances"] = self.db.balances(target)
        elif op == "read_notifications":
            state["notifications"] = self.pilot.notifications(user)
        return hashlib.sha256(encode(state).encode()).hexdigest()

    def normalize(self, op, target, data, user):
        require(op in LABELS, "不支持的操作")
        if op == "apply_leave":
            require(target is None, "新请假无需指定已有申请，请核对后重新发起")
        if op in BUSINESS_CREATE_OPS:
            require(target is None, "新申请无需指定已有申请，请核对后重新发起")
        require(len(encode(data)) < 16000, "操作内容过长")
        require("initialPassword" not in data and "initial_password" not in data, "密码请在确认卡片的专用输入框填写，不要发给助手")
        leave = self.target_leave(op, target, user)
        business = self.target_business(op, target, user)
        data = dict(data)
        if op == "edit_leave":
            data = {
                **{k: leave.get(k) for k in ("leaveType", "startAt", "endAt", "reason", "handoverUser", "handoverNotes")}, **data}
        if op == "update_employee":
            employee = self.db.user(target)
            require(employee, "员工不存在", 404)
            data = {**{f.alias: employee.get(f.alias) for f in EmployeeInput.model_fields.values()}, **data}
        if leave and op in ("edit_leave", "submit", "approve", "reject", "request_information", "withdraw", "transfer", "cancel_leave", "transfer_cancellation"):
            data["version"] = leave["version"]
        if business and op in BUSINESS_SUBMIT_OPS:
            data["version"] = business["version"]
        if op == "adjust_balance":
            target = data.get("userId") or target
            data.update(userId=target, operationId=str(uuid4()))
        if op == "onboard_employee":
            data["initialPassword"] = "Preview-only-password"
        schema = SCHEMAS.get(op)
        if schema:
            try:
                data = schema.model_validate(data).wire(exclude={"initial_password"} if op == "onboard_employee" else set())
            except ValidationError as error:
                fields = "、".join(".".join(map(str, x["loc"])) for x in error.errors())
                raise BusinessError("请补充或修正这些字段：" + fields, 400) from None
        else:
            require(not data, "该操作不接受额外参数")
        return target, data

    def sync_execute(self, op, target, data, user, password_hash=None):
        if op == "create_leave":
            return {"leave": self.leaves.create(user, data)}
        if op == "edit_leave":
            return {"leave": self.leaves.edit(target, user, data)}
        if op in ("approve", "reject", "request_information"):
            return {"leave": self.leaves.decide(target, user, op, data["reason"], data["version"])}
        if op == "withdraw":
            return {"leave": self.leaves.withdraw(target, user, data["reason"], data["version"])}
        if op == "delete_leave":
            self.leaves.delete(target, user)
            return {}
        if op == "remind":
            self.pilot.remind(target, user)
            return {}
        if op == "transfer":
            return {"leave": self.pilot.transfer(target, user, data)}
        if op == "cancel_leave":
            return {"leave": self.pilot.cancel(target, user, data)}
        if op == "transfer_cancellation":
            row = self.db.one("SELECT request_id FROM leave_cancellations WHERE id=?", target)
            return {"leave": self.pilot.transfer(row["request_id"], user, data, target)}
        if op.endswith("_cancellation"):
            decision = {"approve_cancellation": "approved", "reject_cancellation": "rejected", "withdraw_cancellation": "withdrawn"}[op]
            return {"leave": self.pilot.decide_cancel(target, user, decision, data["reason"])}
        if op == "remove_attachment":
            return {"leave": self.pilot.remove_attachment(target, user)}
        if op == "save_calendar":
            return {"days": self.pilot.save_day(user, data)}
        if op == "reset_calendar":
            self.pilot.delete_day(user, target)
            return {}
        if op == "adjust_balance":
            return self.pilot.adjust_balance(user, data)
        if op == "read_notifications":
            self.db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", now_iso(), user["id"])
            return {}
        if op == "update_employee":
            return {"employee": self.organization.update_employee(user, target, data)}
        if op == "onboard_employee":
            return {"employee": self.organization.onboard(user, data, password_hash or "preview-only-never-committed")}
        if op == "save_department":
            return {"department": self.organization.save_department(user, data, target)}
        if op == "create_position":
            return {"position": self.organization.create_position(user, data["name"])}
        if op == "create_role":
            return {"role": self.organization.create_role(user, data)}
        if op == "create_travel":
            return {"travel": self.travel.create(user, data)}
        if op == "submit_travel":
            return {"travel": self.travel.submit(target, user, data.get("version"))}
        if op == "withdraw_travel":
            return {"travel": self.travel.withdraw(target, user, data.get("reason") or "申请人撤回")}
        if op == "create_procurement":
            return {"procurement": self.procurement.create(user, data)}
        if op == "submit_procurement":
            return {"procurement": self.procurement.submit(target, user, data.get("version"))}
        if op == "withdraw_procurement":
            return {"procurement": self.procurement.withdraw(target, user, data.get("reason") or "申请人撤回")}
        if op == "create_overtime":
            return {"overtime": self.overtime.create_overtime(user, data)}
        if op == "submit_overtime":
            return {"overtime": self.overtime.submit_overtime(target, user, data.get("version"))}
        if op == "withdraw_overtime":
            return {"overtime": self.overtime.withdraw_overtime(target, user, data.get("reason") or "申请人撤回")}
        if op == "create_comp_time":
            return {"compTime": self.overtime.create_comp_time(user, data)}
        if op == "submit_comp_time":
            return {"compTime": self.overtime.submit_comp_time(target, user, data.get("version"))}
        if op == "withdraw_comp_time":
            return {"compTime": self.overtime.withdraw_comp_time(target, user, data.get("reason") or "申请人撤回")}
        raise BusinessError("不支持的操作")

    def display(self, key, value):
        if value is None:
            return "未设置"
        if key in ("startAt", "endAt"):
            return parse_time(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
        if key in ("approverId", "userId", "managerId", "leaderId", "leaveApproverId"):
            employee = self.db.user(value)
            return f"{employee['name']}（{value}）" if employee else str(value)
        if key in ("departmentId", "parentId", "role"):
            values = self.db.roles() if key == "role" else self.db.departments()
            return next((v["name"] for v in values if v["id"] == value), str(value))
        if key == "leaveType":
            return {"annual": "年假", "sick": "病假", "personal": "事假"}.get(value, value)
        if key in ("transportStandard", "accommodationStandard", "compensationType", "currency"):
            return {
                "economy": "经济舱", "high_speed": "高铁二等座", "business": "商务舱",
                "none": "无需住宿", "standard": "标准住宿", "premium": "高级住宿",
                "comp_leave": "调休", "pay": "加班费", "CNY": "人民币", "USD": "美元", "EUR": "欧元",
            }.get(value, value)
        if key == "travelerIds":
            return "、".join(self.display("userId", user_id) for user_id in value) or "无同行人"
        if key == "items":
            return "；".join(f"{item['name']} × {item['quantity']}，单价 {item['unitPrice']}" for item in value)
        if key == "status":
            return {"active": "启用", "inactive": "停用", **STATE_LABELS}.get(value, value)
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, dict):
            return encode(value)
        return str(value)

    def prepare(self, conversation, user, op, target=None, data=None):
        user = self.user(user)
        require(op in LABELS, "不支持的操作")
        require(op in self.agent_assignment(user).operations, "当前账号没有办理此事项的权限", 403)
        target, data = self.normalize(op, target, data or {}, user)
        before = self.target_leave(op, target, user)
        business = self.target_business(op, target, user)
        result = {}
        if op == "submit":
            _, manager = self.leaves.validate_submission(before, user)
            result["approver"] = manager["name"]
        elif op in BUSINESS_SUBMIT_OPS:
            manager = self.organization.require_approver(user)
            try:
                with self.db.transaction():
                    result = self.sync_execute(op, target, data, user)
                    raise PreviewRollback()
            except PreviewRollback:
                pass
            result["approver"] = manager["name"]
        else:
            # All these services write only SQLite. Roll back the entire validation preview,
            # including ledger, audit and notification rows. Never await inside this transaction.
            try:
                with self.db.transaction():
                    if op == "apply_leave":
                        leave = self.leaves.create(user, data)
                        _, manager = self.leaves.validate_submission(leave, user)
                        result = {"leave": {**leave, "status": "human_reviewing"}, "approver": manager["name"]}
                    else:
                        result = self.sync_execute(op, target, data, user)
                    raise PreviewRollback()
            except PreviewRollback:
                pass
        fingerprint = self.fingerprint(op, target, user)
        conversation.pop("clarification", None)
        if op in ("apply_leave", "create_leave"):
            conversation.pop("draftInputs", None)
        old = self.pending(conversation)
        if old and old["status"] == "pending" and old["operation"] == op and old["target_id"] == target and old["payload"] == encode(data) and old["fingerprint"] == fingerprint:
            return self.public(old)
        labels = dict(FIELD_LABELS)
        if op == "create_procurement":
            labels["title"] = "采购标题"
        if op == "create_comp_time":
            labels["hours"] = "调休时长（小时）"
        fields = [{"label": labels.get(k, k), "value": self.display(k, v)} for k, v in data.items()
                  if k not in ("version", "operationId")]
        if before:
            fields = [{"label": "申请", "value": f"{before['applicantName']} · {self.display('leaveType', before['leaveType'])} · {before['id']}"},
                      {"label": "原申请时段", "value": f"{self.display('startAt', before['startAt'])} 至 {self.display('endAt', before['endAt'])}（{before['durationHours']} 小时）"},
                      {"label": "原申请原因", "value": before["reason"]}] + fields
        elif business:
            kind = "出差" if op in TRAVEL_OPS else "采购" if op in PROCUREMENT_OPS else "加班" if "overtime" in op else "调休"
            fields = [
                {"label": "申请", "value": f"{kind}申请 · {business['id']}"},
                {"label": "当前状态", "value": self.display("status", business["status"])},
            ] + fields
        if op.endswith("_cancellation"):
            cancellation = next(c for c in self.pilot.cancellations(before["id"]) if c["id"] == target)
            fields.extend([
                {"label": "销假时段", "value": f"{self.display('startAt', cancellation['startAt'])} 至 {self.display('endAt', cancellation['endAt'])}"},
                {"label": "销假时长", "value": f"{cancellation['hours']} 小时"},
                {"label": "销假原因", "value": cancellation["reason"]},
                {"label": "当前销假审批人", "value": self.display("approverId", cancellation["approverId"])},
            ])
        if op == "cancel_leave":
            fields.append({"label": "销假时长", "value": f"{hours(clip(before['workSegments'], data['startAt'], data['endAt']))} 小时"})
        if op == "remove_attachment":
            attachment = self.db.one("SELECT filename FROM leave_attachments WHERE id=?", target)
            fields.append({"label": "删除材料", "value": attachment["filename"]})
        if result.get("leave"):
            fields.append({"label": "操作后状态", "value": self.display("status", result["leave"]["status"])})
            fields.append({"label": "申请时长", "value": f"{result['leave']['durationHours']} 小时"})
        for result_key in ("travel", "procurement", "overtime", "compTime"):
            request = result.get(result_key)
            if request:
                fields.append({"label": "操作后状态", "value": self.display("status", request["status"])})
        if op == "update_employee":
            old_employee = self.db.user(target)
            fields = [{"label": "员工", "value": f"{old_employee['name']}（{target}）"}] + [
                {"label": FIELD_LABELS.get(k, k), "value": f"{self.display(k, old_employee.get(k))} → {self.display(k, v)}"}
                for k, v in data.items() if old_employee.get(k) != v]
        if op in ("apply_leave", "submit", *BUSINESS_SUBMIT_OPS):
            fields.append({"label": "审批人", "value": result["approver"]})
        if op == "reset_calendar":
            fields.append({"label": "日期", "value": target})
        if op == "read_notifications":
            fields.append({"label": "标记范围", "value": f"当前账号的 {self.pilot.notifications(user)['unread']} 条未读通知"})
        if op == "save_department":
            department = next((d for d in self.db.departments() if d["id"] == target), None)
            fields.insert(0, {"label": "操作对象", "value": f"修改部门：{department['name']}（{target}）" if department else "新建部门"})
        preview = {"title": LABELS[op], "fields": fields,
                   "note": "核对后确认执行。审批结果以你的身份留痕。" if "approve" in op or "reject" in op else "核对后确认执行；确认前不会产生业务变更。"}
        if op == "apply_leave":
            preview["note"] = "确认后将创建申请并直接提交给审批人，无需再次提交；最终是否批准由审批人决定。"
        elif op in BUSINESS_SUBMIT_OPS:
            preview["note"] = "确认后将提交申请给审批人；最终是否批准由审批人决定。"
        key = str(uuid4())
        with self.db.transaction():
            self.db.execute("UPDATE assistant_actions SET status='cancelled',updated_at=? WHERE conversation_id=? AND status='pending'", now_iso(), conversation["id"])
            self.db.execute("""INSERT INTO assistant_actions
                (id,conversation_id,user_id,operation,target_id,payload,fingerprint,preview,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", key, conversation["id"], user["id"], op, target, encode(data), fingerprint, encode(preview), now_iso(), now_iso())
        if before and op not in BUSINESS_REQUEST_OPS:
            conversation["contextId"] = before["id"]
        return self.public(self.action(key, user))

    async def confirm(self, key, user, password=None):
        user = self.user(user)
        row = self.action(key, user)
        if row["status"] == "succeeded":
            return self.public(row)
        require(row["status"] == "pending", "操作已处理或正在执行，请刷新对话", 409)
        require(datetime.now(timezone.utc) - parse_time(row["created_at"]) < timedelta(minutes=30),
                "预览已超过 30 分钟，请重新发起操作", 409)
        op, target, data = row["operation"], row["target_id"], json.loads(row["payload"])
        require(op in self.agent_assignment(user).operations, "当前账号没有办理此事项的权限", 403)
        require(row["fingerprint"] == self.fingerprint(op, target, user), "业务数据已变化，请重新发起操作并核对最新预览", 409)
        password_hash = None
        if op == "onboard_employee":
            require(password, "请在卡片中填写初始密码")
            password_hash = hash_password(password)
        try:
            if op == "apply_leave":
                # Creation, submission and the action's durable target commit together.
                # A failed validation leaves no orphan draft; no transaction spans model I/O.
                with self.db.transaction():
                    data["_eventId"] = self.db.one("SELECT COALESCE(MAX(id),0) AS id FROM approval_actions")["id"]
                    leave = self.leaves.create(user, data)
                    submission = self.leaves.start_submission(leave["id"], user, leave["version"])
                    self.db.execute("UPDATE assistant_actions SET status='executing',target_id=?,payload=?,updated_at=? WHERE id=?",
                                    leave["id"], encode(data), now_iso(), key)
                result = {"leave": await self.leaves.review_submission(*submission)}
                with self.db.transaction():
                    self.complete(key, op, result)
            elif op == "submit":
                data["_eventId"] = self.db.one("SELECT COALESCE(MAX(id),0) AS id FROM approval_actions")["id"]
                self.db.execute("UPDATE assistant_actions SET status='executing',payload=?,updated_at=? WHERE id=?", encode(data), now_iso(), key)
                result = {"leave": await self.leaves.submit(target, user, data["version"])}
                with self.db.transaction():
                    self.complete(key, op, result)
            else:
                with self.db.transaction():
                    result = self.sync_execute(op, target, data, user, password_hash)
                    self.complete(key, op, result)
        except BusinessError as error:
            self.db.execute("UPDATE assistant_actions SET status='failed',result=?,updated_at=? WHERE id=?", encode({"message": str(error)}), now_iso(), key)
            raise
        return self.public(self.action(key, user))

    def complete(self, key, op, result):
        result = {**result, "message": LABELS[op] + "已完成。"}
        if op == "apply_leave":
            leave = result["leave"]
            manager = self.db.user(leave["currentApproverId"])
            result["message"] = f"请假申请已提交给{manager['name'] if manager else '审批人'}，当前状态：{self.display('status', leave['status'])}。"
        if op in BUSINESS_REQUEST_OPS:
            request = next(result[k] for k in ("travel", "procurement", "overtime", "compTime") if k in result)
            result["message"] += f"申请编号：{request['id']}；当前状态：{self.display('status', request['status'])}。"
            if op in BUSINESS_CREATE_OPS:
                result["message"] += "可继续要求提交审批，核对提交卡片后确认。"
        self.db.execute("UPDATE assistant_actions SET status='succeeded',result=?,updated_at=? WHERE id=?", encode(result), now_iso(), key)

    def cancel(self, key, user):
        row = self.action(key, user)
        require(row["status"] in ("pending", "cancelled"), "该操作已处理，不能取消", 409)
        self.db.execute("UPDATE assistant_actions SET status='cancelled',updated_at=? WHERE id=?", now_iso(), key)
        return self.public(self.action(key, user))


def install_assistant(app, db, auth, leaves, pilot, organization, conversations, current_user,
                      travel=None, procurement=None, overtime=None):
    service = AssistantService(db, leaves, pilot, organization, conversations, travel, procurement, overtime)
    app.state.assistant_service = service

    def record(conversation, content, outcome=None):
        conversation["messages"].append({"role": "assistant", "content": content, "outcome": outcome})
        conversations.save(conversation)

    @app.get("/api/agent/catalog")
    async def catalog(user=Depends(current_user)):
        assignment = service.agent_assignment(user)
        allowed = assignment.operations
        items = []
        for op, title in LABELS.items():
            if op not in allowed:
                continue
            schema = SCHEMAS[op].model_json_schema() if op in SCHEMAS else {"properties": {}}
            properties = schema.get("properties", {})
            for field in ("version", "operationId", "initialPassword"):
                properties.pop(field, None)
            schema["required"] = [x for x in schema.get("required", []) if x in properties]
            if op in ("edit_leave", "update_employee"):
                schema["required"] = []
            labels = dict(FIELD_LABELS)
            if op == "create_procurement":
                labels["title"] = "采购标题"
            if op == "create_comp_time":
                labels["hours"] = "调休时长（小时）"
            target = (None if op in BUSINESS_CREATE_OPS else "leave" if op in LEAVE_OPS else "travel" if op in TRAVEL_OPS
                      else "procurement" if op in PROCUREMENT_OPS else "overtime" if op in OVERTIME_OPS
                      else "cancellation" if op.endswith("_cancellation")
                      else "employee" if op == "update_employee" else "department" if op == "save_department"
                      else "attachment" if op == "remove_attachment" else "day" if op == "reset_calendar" else None)
            items.append({"operation": op, "title": title, "schema": schema, "target": target, "labels": labels})
        return {"items": items, "labels": FIELD_LABELS, "agent": assignment.public(),
                "directory": service.query(user, "organization")}

    @app.get("/api/agent/workspace/{conversation_id}")
    async def workspace(conversation_id: str, user=Depends(current_user)):
        conversation = conversations.get(user["id"], conversation_id)
        return service.snapshot(conversation, user)

    @app.post("/api/agent/workspace", status_code=201)
    async def create_workspace(user=Depends(current_user)):
        return service.snapshot(conversations.create(user["id"]), user)

    @app.post("/api/agent/workspace/{conversation_id}/pause")
    async def pause_workspace(conversation_id: str, user=Depends(current_user)):
        conversation = conversations.get(user["id"], conversation_id)
        if conversation.get("runStatus") == "paused" and not conversation["busy"]:
            return service.snapshot(conversation, user)
        control = AGENT_RUNS.request_pause(user["id"], conversation_id)
        require(control, "当前对话没有可暂停的运行任务", 409)
        try:
            await asyncio.wait_for(control.stopped.wait(), timeout=10)
        except TimeoutError:
            return {
                **service.snapshot(conversation, user),
                "message": "暂停请求已提交，将在当前原子步骤完成后生效。",
            }
        return service.snapshot(conversations.get(user["id"], conversation_id), user)

    @app.post("/api/agent/messages")
    async def message(data: ChatInput, user=Depends(current_user)):
        from .assistant_agent import respond
        conversation = conversations.begin(user["id"], data.conversation_id, rotate=True)
        display_message = data.message
        paused_message = conversation.get("pausedMessage")
        if conversation.get("runStatus") == "paused" and paused_message:
            if data.message.strip() in ("继续", "继续执行", "恢复", "恢复执行"):
                run_message = f"继续处理暂停前的请求：\n{paused_message}"
            else:
                run_message = f"暂停前的请求：\n{paused_message}\n\n用户最新补充或修正：\n{data.message}"
        else:
            run_message = data.message
        conversation["runStatus"] = "running"
        control = None
        try:
            # Query cards describe the latest turn only. Keeping an old card on a
            # new conversational turn makes unrelated questions look like queries.
            conversation["cards"] = []
            if data.context_id:
                pilot.view(data.context_id, user)
                conversation["contextId"] = data.context_id
            run_task = asyncio.create_task(
                respond(run_message, user, service, conversation, data.action_id),
                name=f"oa-agent:{user['id']}:{conversation['id']}",
            )
            control = AGENT_RUNS.start(user["id"], conversation["id"], run_task)
            result = await run_task
            conversation["messages"].append({"role": "user", "content": display_message})
            record(conversation, result, "completed" if service.pending(conversation) else None)
            conversation.pop("pausedMessage", None)
            conversation["runStatus"] = "completed"
            return {"message": result, "conversation": {**service.snapshot(conversation, user), "busy": False}}
        except asyncio.CancelledError:
            action = service.pending(conversation)
            if action and action["status"] == "pending":
                service.cancel(action["id"], user)
            conversation["cards"] = []
            conversation.pop("clarification", None)
            conversation["pausedMessage"] = run_message
            conversation["runStatus"] = "paused"
            conversation["messages"].append({"role": "user", "content": display_message})
            record(conversation, "已暂停当前处理。你可以补充新的要求，或发送“继续”重新开始这一轮。")
            return {
                "message": "任务已暂停",
                "conversation": {**service.snapshot(conversation, user), "busy": False},
            }
        finally:
            conversations.finish(conversation)
            if control:
                AGENT_RUNS.finish(control)

    @app.post("/api/agent/prepare")
    async def prepare(data: PrepareInput, user=Depends(current_user)):
        conversation = conversations.begin(user["id"], data.conversation_id, rotate=True)
        try:
            action = service.prepare(conversation, user, data.operation, data.target_id, data.data)
            record(conversation, "请核对：" + action["preview"]["title"])
            return {**service.snapshot(conversation, user), "busy": False}
        finally:
            conversations.finish(conversation)

    @app.post("/api/agent/actions/{action_id}/confirm")
    async def confirm(action_id: str, data: ConfirmInput = ConfirmInput(), user=Depends(current_user)):
        row = service.action(action_id, user)
        conversation = conversations.begin(user["id"], row["conversation_id"], rotate=True)
        try:
            action = await service.confirm(action_id, user, data.initial_password.get_secret_value() if data.initial_password else None)
            leave = (action.get("result") or {}).get("leave")
            if leave:
                conversation["contextId"] = leave["id"]
            if not any(m.get("actionId") == action_id for m in conversation["messages"]):
                conversation["messages"].append({"role": "assistant", "content": action["result"]["message"], "outcome": "completed", "actionId": action_id})
            return {**service.snapshot(conversation, user), "busy": False}
        finally:
            conversations.finish(conversation)

    @app.post("/api/agent/actions/{action_id}/cancel")
    async def cancel(action_id: str, user=Depends(current_user)):
        row = service.action(action_id, user)
        conversation = conversations.begin(user["id"], row["conversation_id"], rotate=True)
        try:
            service.cancel(action_id, user)
            record(conversation, "已取消待执行操作。")
            return {**service.snapshot(conversation, user), "busy": False}
        finally:
            conversations.finish(conversation)

    @app.post("/api/agent/workspace/{conversation_id}/attachments")
    async def upload(conversation_id: str, request: Request, user=Depends(current_user)):
        conversation = conversations.begin(user["id"], conversation_id, rotate=True)
        try:
            target = conversation.get("contextId")
            require(target, "请先选择要补充材料的申请")
            pilot.view(target, user)
            content = bytearray()
            async for chunk in request.stream():
                require(len(content) + len(chunk) <= 5 * 1024 * 1024, "文件不能超过 5 MB", 413)
                content.extend(chunk)
            pilot.upload(target, auth.session(request)["user"], unquote(request.headers.get("x-filename", "")), bytes(content))
            record(conversation, "材料已上传到当前申请。")
            return {**service.snapshot(conversation, user), "busy": False}
        finally:
            conversations.finish(conversation)
