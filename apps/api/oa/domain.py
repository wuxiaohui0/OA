from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints
from pydantic.alias_generators import to_camel

SHANGHAI = ZoneInfo("Asia/Shanghai")
SESSIONS = ((9, 0, 12, 0), (13, 30, 18, 0))
LEAVE_TYPES = {"annual": "年假", "personal": "事假", "sick": "病假"}
TRANSITIONS = {
    "draft": ["submitted", "withdrawn"],
    "submitted": ["validating", "withdrawn"],
    "validating": ["need_information", "agent_reviewing", "human_reviewing", "withdrawn"],
    "need_information": ["draft", "withdrawn"],
    "agent_reviewing": [ "need_information", "human_reviewing", "withdrawn"],
    "human_reviewing": ["approved", "rejected", "need_information", "withdrawn"],
    "approved": ["cancelled"],
    "rejected": ["draft"],
    "withdrawn": ["draft"],
    "cancelled": [],
}


class BusinessError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code or (
            404 if "不存在" in message else 403 if any(word in message for word in ["权限", "只能", "不是该"]) else 400
        )


def require(condition, message, status_code=None):
    if not condition:
        raise BusinessError(message, status_code)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result
    except (ValueError, TypeError, AttributeError):
        raise BusinessError("请假时间格式不正确") from None


def utc_iso(value):
    return parse_time(value).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def working_hours(start_at, end_at):
    start, end = parse_time(start_at).astimezone(SHANGHAI), parse_time(end_at).astimezone(SHANGHAI)
    if end <= start:
        return 0
    day, seconds = start.date(), 0
    while day <= end.date():
        if day.weekday() < 5:
            for sh, sm, eh, em in SESSIONS:
                low = max(start, datetime.combine(day, time(sh, sm), SHANGHAI))
                high = min(end, datetime.combine(day, time(eh, em), SHANGHAI))
                seconds += max(0, (high - low).total_seconds())
        day += timedelta(days=1)
    return round(seconds / 3600, 2)


def assert_transition(before, after):
    require(after in TRANSITIONS.get(before, []), f"不允许从 {before} 转换到 {after}")


POLICY = """系统试点规则（待 HR 按公司制度确认）
1. 工作时段为 09:00-12:00、13:30-18:00，周末不计请假时长。
2. 工作日历中的节假日及调休设置优先于默认工作周；以申请提交时保存的工作时段为准。
3. 试点期间 AI 只提供审核建议，所有申请由指定的请假审批人确认；未单独指定时使用直属领导。
4. 余额不足或与有效请假冲突时不能批准，应先调整额度或退回修改。批准后扣减额度，销假经人工批准后返还。
5. 审批人可退回补充材料，申请人修改后重新提交；已批准申请可申请全部或部分销假。"""
TIME_DEFAULTS = """发起请假时，只提供某个日期而没有限定时段，默认该日全天 09:00-18:00；不必追问开始和结束时间。
明确说上午时用 09:00-12:00，下午时用 13:30-18:00。全天按工作时段扣除午休，共 7.5 小时，不是 24 小时。
明确提供的具体时间优先；只给一个时间点、说半天但未说明上下午、表示时间待定或日期有歧义时，只追问有歧义的部分。
默认时段必须在草稿中展示，仍需员工确认后才能提交。"""
WORKFLOW = (
    """当前支持年假、事假和病假，使用以下请假审批流程：
1. 员工填写请假类型、开始和结束时间、原因及交接信息，创建草稿。
2. 员工确认草稿后提交；提交前必须已配置有效的请假审批人。
3. 系统核验工作时段、假期余额与时间冲突，再按制度进行智能审核；余额不足或时间冲突转人工处理。
4. 智能体提供审核建议，全部进入人工审批。需要补充信息时由审批人退回，员工修改后重新提交。
5. 人工审批人批准或填写理由驳回，结果和操作记录可在申请详情中查看；批准后扣减假期余额。
审批人优先使用员工单独指定的请假审批人，否则使用直属领导，必须是启用且有请假审批权限的其他员工。
董事长等人员可以设置无直属领导，但请假仍需单独指定审批人；未配置时可以保存草稿，不能提交，也不能自己审批。
"""
    + POLICY
)


class InputModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", str_strip_whitespace=True
    )

    def wire(self, **kwargs):
        return self.model_dump(by_alias=True, mode="json", **kwargs)


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class LeaveInput(InputModel):
    leave_type: Literal["annual", "personal", "sick"]
    start_at: str = Field(min_length=1)
    end_at: str = Field(min_length=1)
    reason: str = Field(min_length=2)
    handover_user: str | None = None
    handover_notes: str | None = None


class Permissions(InputModel):
    manage_organization: bool = Field(strict=True)
    manage_accounts: bool = Field(default=False, strict=True)
    lead_team: bool = Field(strict=True)
    approve_leave: bool = Field(strict=True)


class RoleInput(InputModel):
    name: Name
    permissions: Permissions


class PositionInput(InputModel):
    name: Name


class DepartmentInput(InputModel):
    name: Name
    parent_id: Identifier | None
    leader_id: Identifier | None


class EmployeeInput(InputModel):
    name: str = Field(min_length=1, max_length=80)
    employee_no: Identifier
    department_id: Identifier
    title: Name
    role: Identifier
    manager_id: Identifier | None
    no_manager: bool = Field(default=False, strict=True)
    leave_approver_id: Identifier | None = None
    hired_at: str | None
    status: Literal["active", "inactive"]


class Entitlements(InputModel):
    annual: float = Field(ge=0, le=2000, strict=True)
    personal: float = Field(ge=0, le=2000, strict=True)
    sick: float = Field(ge=0, le=2000, strict=True)


class OnboardInput(EmployeeInput):
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
    initial_password: SecretStr = Field(min_length=12, max_length=128)
    role: Identifier = "employee"
    manager_id: Identifier | None = None
    hired_at: str
    status: Literal["active"] = "active"
    leave_entitlements: Entitlements


class MessageInput(InputModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class DecisionInput(InputModel):
    reason: str = Field(default="", max_length=2000)
    version: int | None = Field(default=None, ge=1)


class LeaveEditInput(LeaveInput):
    version: int = Field(ge=1)


class ReasonInput(InputModel):
    reason: str = Field(min_length=2, max_length=2000)


class VersionReasonInput(ReasonInput):
    version: int = Field(ge=1)


class TransferInput(VersionReasonInput):
    approver_id: Identifier


class CancellationInput(VersionReasonInput):
    start_at: str
    end_at: str


class CalendarDayInput(InputModel):
    day: str
    is_workday: bool = Field(strict=True)
    name: str = Field(min_length=1, max_length=100)


class BalanceAdjustmentInput(ReasonInput):
    user_id: Identifier
    leave_type: Literal["annual", "personal", "sick"]
    hours: float = Field(ge=-2000, le=2000, allow_inf_nan=False)
    operation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


class Review(InputModel):
    decision: Literal["approve", "need_information", "escalate"]
    summary: str
    reason: str
    missing_fields: list[str]
    risk_flags: list[str]
    policy_references: list[str]
    confidence: float = Field(ge=0, le=1)


class AssistantResponse(InputModel):
    status: Literal["draft_created", "needs_information", "approval_completed", "rejection_completed", "no_action"]
    message: str = Field(min_length=1)
    missing_fields: list[str]
