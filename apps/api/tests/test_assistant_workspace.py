import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import api, auth_headers, create, pending
from oa.app import create_app
from oa import assistant_agent
from oa.assistant import ADMIN_OPS, APPROVAL_OPS
from oa.agents import deterministic_review
from oa.domain import BusinessError, SHANGHAI, now_iso
from test_agents import gateway, structured, tool_reply  # noqa: F401


@pytest.fixture(autouse=True)
def workspace_reviewer(app):
    # These tests exercise the conversation model; review-model integration is tested separately.
    async def review(*args):
        return deterministic_review(*args)
    app.state.leave_service.reviewer = review


def chat(client, message="帮助", conversation=None, user="u1001", **extra):
    return api(client, "POST", "/agent/messages", {
        "message": message, "conversationId": conversation, **extra}, user)["conversation"]


def prepare(client, op, target=None, data=None, user="u1001", conversation=None, status=200):
    conversation = conversation or chat(client, user=user)["id"]
    return api(client, "POST", "/agent/prepare", {"conversationId": conversation,
        "operation": op, "targetId": target, "data": data or {}}, user, status)


def confirm(client, workspace, user="u1001", status=200, data=None):
    return api(client, "POST", f"/agent/actions/{workspace['action']['id']}/confirm", data or {}, user, status)


def tomorrow_workday(app):
    day = (datetime.now(SHANGHAI) + timedelta(days=1)).date().isoformat()
    app.state.db.execute("INSERT OR REPLACE INTO work_calendar VALUES(?,1,'测试工作日','u3001',?)", day, now_iso())
    return day


def test_role_catalog_and_backend_agree(client, app):
    employee = {item["operation"] for item in api(client, "GET", "/agent/catalog")["items"]}
    assert not employee & (APPROVAL_OPS | ADMIN_OPS)
    assert {"apply_leave", "create_leave", "edit_leave", "submit", "withdraw", "cancel_leave"} <= employee
    manager = {item["operation"] for item in api(client, "GET", "/agent/catalog", user="u2001")["items"]}
    assert APPROVAL_OPS <= manager and not manager & ADMIN_OPS
    hr = {item["operation"] for item in api(client, "GET", "/agent/catalog", user="u3001")["items"]}
    assert APPROVAL_OPS | ADMIN_OPS <= hr
    for operation in APPROVAL_OPS | ADMIN_OPS:
        prepare(client, operation, status=403)
    assert not app.state.db.all("SELECT id FROM assistant_actions")
    # Capability flags, rather than the built-in role name, control availability.
    app.state.db.execute("UPDATE permission_roles SET can_approve_leave=1 WHERE id='employee'")
    custom = {item["operation"] for item in api(client, "GET", "/agent/catalog")["items"]}
    assert APPROVAL_OPS <= custom


def test_enterprise_agent_profiles_bind_least_privilege_tools_and_skills(client, app, admin):
    employee = api(client, "GET", "/agent/catalog")
    assert employee["agent"]["profileId"] == "employee"
    assert employee["agent"]["instanceId"] == "employee:u1001"
    assert "approval.prepare" not in employee["agent"]["tools"]
    assert [skill["id"] for skill in employee["agent"]["skills"]] == [
        "leave-self-service", "policy-guidance"
    ]

    leader = api(client, "GET", "/agent/catalog", user="u2001")["agent"]
    assert leader["profileId"] == "approval-leader"
    assert {"team.query", "approval.query", "approval.prepare"} <= set(leader["tools"])
    assert "organization.prepare" not in leader["tools"]
    assert leader["scope"]["directReportCount"] == 2

    hr = api(client, "GET", "/agent/catalog", user="u3001")["agent"]
    assert hr["profileId"] == "hr-governance"
    assert {"approval.prepare", "organization.query", "organization.prepare"} <= set(hr["tools"])
    assert {"approval-governance", "organization-governance"} <= {
        skill["id"] for skill in hr["skills"]
    }

    administrator = api(client, "GET", "/agent/catalog", user=admin)["agent"]
    assert administrator["profileId"] == "enterprise-admin"
    assert "account.prepare" in administrator["tools"]
    assert "account-governance" in {skill["id"] for skill in administrator["skills"]}

    # A team-lead capability does not implicitly grant approval tools.
    app.state.db.execute(
        "INSERT INTO permission_roles VALUES('team-only','团队负责人',0,1,0,0)"
    )
    app.state.db.execute("UPDATE users SET role='team-only' WHERE id='u1002'")
    team = api(client, "GET", "/agent/catalog", user="u1002")["agent"]
    assert team["profileId"] == "team-leader"
    assert "team.query" in team["tools"]
    assert "approval.prepare" not in team["tools"]
    assert not ({"approve", "reject"} & {
        item["operation"] for item in api(client, "GET", "/agent/catalog", user="u1002")["items"]
    })


def test_model_agent_binding_denies_unassigned_query_and_registers_leader_tools(
    client, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    observed = {}

    def build(name, tools, schema, prompt):
        observed[name] = {"tools": {item.name: item for item in tools}, "prompt": prompt}
        return name

    async def invoke(agent, messages):
        if agent == "employee:u1001":
            denied = await observed[agent]["tools"]["query_oa"].ainvoke(
                {"resource": "requests", "scope": "all"}
            )
            assert denied == {"ok": False, "message": "当前智能体未绑定该查询能力"}
        return {"structured_response": {
            "status": "no_action", "message": "未执行操作。", "missingFields": []
        }}

    monkeypatch.setattr(assistant_agent, "build_agent", build)
    monkeypatch.setattr(assistant_agent, "invoke", invoke)
    chat(client, "分析一下我近期的安排")
    chat(client, "分析一下团队近期的安排", user="u2001")

    employee_tools = observed["employee:u1001"]["tools"]
    leader_tools = observed["approval-leader:u2001"]["tools"]
    assert "query_approval_workbench" not in employee_tools
    assert {"query_approval_workbench", "prepare_approval_action", "query_team_directory"} <= set(leader_tools)
    assert "审批治理" not in observed["employee:u1001"]["prompt"]
    assert "审批治理" in observed["approval-leader:u2001"]["prompt"]


def test_configured_agent_handles_greeting_instead_of_local_shortcut(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    observed = {}

    def build(name, tools, schema, prompt):
        observed["name"] = name
        observed["prompt"] = prompt
        return object()

    async def invoke(_agent, messages):
        observed["messages"] = messages
        return {"structured_response": {
            "status": "no_action", "message": "你好，今天需要我协助什么 OA 事项？", "missingFields": []
        }}

    monkeypatch.setattr(assistant_agent, "build_agent", build)
    monkeypatch.setattr(assistant_agent, "invoke", invoke)
    workspace = chat(client, "你好")

    assert observed["name"] == "employee:u1001"
    assert observed["messages"][-1] == {"role": "user", "content": "你好"}
    assert "每一轮普通对话都由你理解并回复" in observed["prompt"]
    assert workspace["messages"][-1]["content"] == "你好，今天需要我协助什么 OA 事项？"


def test_simple_date_question_uses_agent_context_not_work_calendar(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    observed = {}

    def build(_name, tools, _schema, prompt):
        observed["tools"] = {item.name: item for item in tools}
        observed["prompt"] = prompt
        return object()

    async def invoke(*_args):
        observed["datetime"] = await observed["tools"]["get_current_datetime"].ainvoke({})
        current = observed["datetime"]
        return {"structured_response": {
            "status": "no_action",
            "message": f"今天是 {current['date']}，{current['weekday']}。",
            "missingFields": [],
        }}

    monkeypatch.setattr(assistant_agent, "build_agent", build)
    monkeypatch.setattr(assistant_agent, "invoke", invoke)
    workspace = chat(client, "今天几号")

    now = datetime.now(SHANGHAI)
    assert observed["datetime"]["timezone"] == "Asia/Shanghai"
    assert "get_current_datetime" in observed["prompt"]
    assert workspace["cards"] == []
    assert now.date().isoformat() in workspace["messages"][-1]["content"]
    assert "智能理解暂不可用" not in workspace["messages"][-1]["content"]


def test_internal_leave_calendar_query_does_not_render_calendar_card(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    tools = {}

    def build(_name, registered, _schema, _prompt):
        tools.update({item.name: item for item in registered})
        return object()

    async def invoke(*_args):
        calendar = await tools["query_oa"].ainvoke({"resource": "calendar"})
        assert calendar["interpretation"].startswith("days 仅包含特殊日期设置")
        return {"structured_response": {
            "status": "needs_information", "message": "请补充请假日期。", "missingFields": ["请假日期"]
        }}

    monkeypatch.setattr(assistant_agent, "build_agent", build)
    monkeypatch.setattr(assistant_agent, "invoke", invoke)
    workspace = chat(client, "帮我办理请假")
    assert workspace["cards"] == []
    assert workspace["messages"][-1]["content"] == "请补充请假日期。"


def test_calendar_card_explains_empty_overrides_as_default_week(client, gateway):  # noqa: F811
    steps, _ = gateway
    steps.extend([
        tool_reply("query_oa", {"resource": "calendar", "present": True}),
        structured({"status": "no_action", "message": "已查询工作日历。", "missingFields": []}),
    ])
    workspace = chat(client, "工作日历")
    calendar = workspace["cards"][0]
    assert calendar["kind"] == "calendar"
    assert calendar["defaultWorkdays"] == ["周一", "周二", "周三", "周四", "周五"]
    assert calendar["defaultSessions"] == ["09:00-12:00", "13:30-18:00"]
    assert "并不表示没有工作日" in calendar["interpretation"]


def test_smalltalk_is_natural_and_does_not_keep_stale_query_cards(client, gateway):  # noqa: F811
    steps, _ = gateway
    steps.extend([
        tool_reply("query_oa", {"resource": "notifications", "present": True}),
        structured({"status": "no_action", "message": "已查询消息通知。", "missingFields": []}),
        structured({"status": "no_action", "message": "你好，陈默。今天想查点什么，还是要办一件事？", "missingFields": []}),
        structured({"status": "no_action", "message": "我是你的 OA 智能助手，会按你的身份和权限协助办理，但不会替你确认。", "missingFields": []}),
        structured({"status": "no_action", "message": "我是企业内部的 OA 智能体，底层模型由平台统一配置。", "missingFields": []}),
    ])
    workspace = chat(client, "消息通知")
    assert workspace["cards"][0]["kind"] == "notifications"

    workspace = chat(client, "你号", workspace["id"])
    assert workspace["cards"] == []
    assert workspace["messages"][-2] == {"role": "user", "content": "你号"}
    assert workspace["messages"][-1]["content"] == "你好，陈默。今天想查点什么，还是要办一件事？"

    workspace = chat(client, "你是谁", workspace["id"])
    assert workspace["cards"] == []
    assert "OA 智能助手" in workspace["messages"][-1]["content"]
    assert "不会替你确认" in workspace["messages"][-1]["content"]

    workspace = chat(client, "你是什么模型", workspace["id"])
    answer = workspace["messages"][-1]["content"]
    assert "企业内部的 OA 智能体" in answer
    assert "Codex" not in answer and "GPT-5" not in answer


@pytest.mark.parametrize("message", [
    "请假明天，原因是家中又是",
    "明天请假，原因是昨天没休息号，要休息。",
])
def test_unconfigured_model_fails_closed_without_local_intent_routing(
    client, app, monkeypatch, message
):
    def no_model(*args, **kwargs):
        pytest.fail("An unconfigured runtime must not construct a model")
    monkeypatch.setattr(assistant_agent, "build_agent", no_model)
    workspace = chat(client, message)
    assert workspace["action"] is None
    assert workspace["cards"] == []
    assert not app.state.db.list_leaves("u1001")["mine"]
    assert "模型尚未配置" in workspace["messages"][-1]["content"]


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
def test_model_failure_fails_closed_without_keyword_fallback(client, app, monkeypatch, failure):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(assistant_agent, "build_agent", lambda *args: object())
    calls = []
    async def broken(*args):
        calls.append(True)
        raise failure("Upstream failure should not be shown to the user")
    monkeypatch.setattr(assistant_agent, "invoke", broken)
    tomorrow_workday(app)
    workspace = chat(client, "帮我办理请假")
    assert workspace["action"] is None
    assert "暂时不可用" in workspace["messages"][-1]["content"]
    assert "Upstream failure" not in workspace["messages"][-1]["content"]
    workspace = chat(client, "明天，原因：家里有事", workspace["id"])
    assert workspace["action"] is None and len(calls) == 2
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_model_catalog_is_scoped_and_timeout_preserves_prepared_action(client, app, monkeypatch, leave_input):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    available_tools = {}
    def build(name, tools, schema, prompt):
        catalog = json.loads(prompt.split("当前账号操作目录：", 1)[1])
        assert not set(catalog) & (APPROVAL_OPS | ADMIN_OPS)
        available_tools.update({tool.name: tool for tool in tools})
        return object()
    async def partial(*args):
        await available_tools["apply_leave"].ainvoke(leave_input)
        raise TimeoutError()
    monkeypatch.setattr(assistant_agent, "build_agent", build)
    monkeypatch.setattr(assistant_agent, "invoke", partial)
    workspace = chat(client, "帮我办理请假")
    assert workspace["action"]["status"] == "pending"
    assert "请核对" in workspace["messages"][-1]["content"]
    assert len(app.state.db.all("SELECT id FROM assistant_actions")) == 1
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_leave_reason_is_not_treated_as_an_approval_command(
    client, app, gateway, leave_input  # noqa: F811
):
    steps, _ = gateway
    payload = {**leave_input, "reason": "家人申请被驳回，需要协助处理"}
    steps.extend([
        tool_reply("apply_leave", payload),
        structured({"status": "no_action", "message": "请确认请假卡片。", "missingFields": []}),
    ])
    workspace = chat(client, "明天请事假，原因：家人申请被驳回，需要协助处理")
    assert workspace["action"]["operation"] == "apply_leave"
    workspace = confirm(client, workspace)
    assert workspace["context"]["leave"]["reason"] == "家人申请被驳回，需要协助处理"


def test_implicit_reason_preview_can_be_revised_and_requires_confirmation(
    client, app, gateway, leave_input  # noqa: F811
):
    steps, _ = gateway
    steps.extend([
        tool_reply("apply_leave", {**leave_input, "reason": "家中有事"}),
        structured({"status": "no_action", "message": "请确认请假卡片。", "missingFields": []}),
    ])
    first = chat(client, "明天请假，家中有事")
    assert first["action"]["status"] == "pending"
    values = first["action"]["editableData"]
    assert values["reason"] == "家中有事"
    assert not app.state.db.list_leaves("u1001")["mine"]
    restored = api(client, "GET", "/agent/workspace/" + first["id"])
    assert restored["action"]["id"] == first["action"]["id"]
    revised = prepare(client, "apply_leave", data={**values, "leaveType": "annual", "reason": "家庭事务"}, conversation=first["id"])
    assert revised["action"]["id"] != first["action"]["id"]
    confirm(client, first, status=409)
    assert not app.state.db.list_leaves("u1001")["mine"]
    confirmed = confirm(client, revised)
    assert confirmed["context"]["leave"]["leaveType"] == "annual"
    assert confirmed["context"]["leave"]["status"] == "human_reviewing"
    confirm(client, revised)
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1
    assert len(app.state.db.list_leaves("u2001")["inbox"]) == 1


def test_model_clarification_choices_keep_context_and_do_not_execute(client, app, gateway, leave_input):  # noqa: F811
    steps, requests = gateway
    steps.extend([tool_reply("query_oa", {"resource": "calendar"}),
        structured({"status": "needs_information", "message": "请选择请假类型：年假、事假或病假。", "missingFields": ["请假类型"]}),
        tool_reply("apply_leave", {**leave_input, "leaveType": "annual"}),
        structured({"status": "no_action", "message": "请确认卡片", "missingFields": []})])
    workspace = chat(client, "帮我安排一次请假")
    assert workspace["action"] is None
    assert workspace["clarification"]["choices"] == ["年假", "事假", "病假"]
    restored = api(client, "GET", "/agent/workspace/" + workspace["id"])
    assert restored["clarification"] == workspace["clarification"]
    api(client, "POST", "/agent/messages", {"message": "确认", "conversationId": workspace["id"]}, status=409)
    workspace = chat(client, "年假", workspace["id"])
    assert workspace["action"]["status"] == "pending"
    assert workspace["action"]["editableData"]["leaveType"] == "annual"
    assert workspace["clarification"] is None
    assert not app.state.db.list_leaves("u1001")["mine"]
    assert any(message.get("content") == "帮我安排一次请假" for message in requests[2]["messages"])
    api(client, "POST", f"/agent/actions/{workspace['action']['id']}/cancel")
    confirm(client, workspace, status=409)
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_create_edit_submit_approve_and_repeat(client, app, leave_input):
    workspace = prepare(client, "create_leave", data=leave_input)
    assert workspace["action"]["operation"] == "create_leave"
    assert not app.state.db.list_leaves("u1001")["mine"]
    workspace = confirm(client, workspace)
    leave = workspace["context"]["leave"]
    assert leave["status"] == "draft"
    repeat = confirm(client, workspace)
    assert repeat["context"]["leave"]["id"] == leave["id"]
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1
    workspace = prepare(
        client,
        "edit_leave",
        leave["id"],
        {"reason": "需要到医院检查"},
        conversation=workspace["id"],
    )
    assert workspace["action"]["operation"] == "edit_leave"
    assert app.state.db.leave(leave["id"])["reason"] == "身体不适需要休息"
    workspace = confirm(client, workspace)
    assert workspace["context"]["leave"]["reason"] == "需要到医院检查"
    workspace = prepare(client, "submit", leave["id"], conversation=workspace["id"])
    workspace = confirm(client, workspace)
    assert workspace["context"]["leave"]["status"] == "human_reviewing"
    manager = prepare(client, "approve", leave["id"], user="u2001")
    assert app.state.db.leave(leave["id"])["status"] == "human_reviewing"
    assert app.state.db.remaining("u1001", "sick") == 80
    manager = confirm(client, manager, "u2001")
    assert manager["context"]["leave"]["status"] == "approved"
    confirm(client, manager, "u2001")
    assert app.state.db.remaining("u1001", "sick") == 72.5
    assert len([a for a in app.state.db.actions(leave["id"]) if a["action"] == "approve"]) == 1


def test_permission_isolation_stale_preview_and_expiry(client, app, leave_input):
    leave = pending(client, leave_input)
    prepare(client, "approve", leave["id"], user="u1002", status=403)
    manager = prepare(client, "approve", leave["id"], user="u2001")
    confirm(client, manager, "u1001", status=404)
    api(client, "GET", "/agent/workspace/" + manager["id"], user="u1001", status=404)
    api(client, "POST", f"/leave-requests/{leave['id']}/withdraw", {"version": leave["version"], "reason": "计划有变"})
    confirm(client, manager, "u2001", status=409)
    workspace = prepare(client, "edit_leave", leave["id"], {"reason": "重新安排休假"})
    app.state.db.execute("UPDATE assistant_actions SET created_at=? WHERE id=?",
        (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), workspace["action"]["id"])
    confirm(client, workspace, status=409)


def test_ambiguous_objects_require_selection_and_confirmation_is_exact(client, app, leave_input):
    first = create(client, leave_input)
    second = create(client, {**leave_input, "reason": "另一条申请"})
    prepare(client, "submit", status=404)
    workspace = prepare(client, "submit", second["id"])
    assert workspace["action"]["targetId"] == second["id"]
    workspace = chat(client, "好的", workspace["id"], actionId=workspace["action"]["id"])
    assert app.state.db.leave(second["id"])["status"] == "draft"
    api(client, "POST", "/agent/messages", {"message": "确认执行", "conversationId": workspace["id"]}, status=409)
    workspace = chat(client, "确认执行", workspace["id"], actionId=workspace["action"]["id"])
    assert workspace["context"]["leave"]["status"] == "human_reviewing"
    assert app.state.db.leave(first["id"])["status"] == "draft"


def test_revise_return_resubmit_partial_cancel_transfer(client, app, leave_input):
    leave = pending(client, leave_input)
    returned = prepare(client, "request_information", leave["id"], {"reason": "请补充检查材料"}, "u2001")
    confirm(client, returned, "u2001")
    edited = prepare(client, "edit_leave", leave["id"], {"reason": "已补充检查材料"})
    confirm(client, edited)
    confirm(client, prepare(client, "submit", leave["id"]))
    transfer = prepare(client, "transfer", leave["id"], {"approverId": "u3001", "reason": "出差交接"}, "u2001")
    confirm(client, transfer, "u2001")
    confirm(client, prepare(client, "approve", leave["id"], user="u3001"), "u3001")
    cancelled = prepare(client, "cancel_leave", leave["id"], {"reason": "提前返岗",
        "startAt": leave_input["startAt"], "endAt": leave_input["startAt"][:10] + "T12:00:00+08:00"})
    workspace = confirm(client, cancelled)
    cancel = workspace["context"]["cancellations"][0]
    assert cancel["hours"] == 3
    assert cancel["approverId"] == "u2001"
    routed = prepare(client, "transfer_cancellation", cancel["id"], {"reason": "出差交接", "approverId": "u3001"}, "u2001")
    confirm(client, routed, "u2001")
    approved = prepare(client, "approve_cancellation", cancel["id"], {"reason": "确认返岗"}, "u3001")
    confirm(client, approved, "u3001")
    confirm(client, approved, "u3001")
    assert app.state.db.remaining("u1001", "sick") == 75.5


def test_hr_preview_has_no_side_effect_and_changes_are_atomic(client, app, leave_input, employee_input):
    day = leave_input["startAt"][:10]
    before = app.state.pilot.calendar()
    prepared = prepare(client, "save_calendar", data={"day": day, "isWorkday": False, "name": "公司假日"}, user="u3001")
    assert app.state.pilot.calendar() == before
    confirm(client, prepared, "u3001")
    assert app.state.pilot.calendar()[0]["name"] == "公司假日"
    adjust = prepare(client, "adjust_balance", data={"userId": "u1001", "leaveType": "annual", "hours": 8, "reason": "年度核定"}, user="u3001")
    balance = app.state.db.remaining("u1001", "annual")
    confirm(client, adjust, "u3001")
    confirm(client, adjust, "u3001")
    assert app.state.db.remaining("u1001", "annual") == balance + 8
    prepare(client, "adjust_balance", data={"userId": "u1001", "leaveType": "annual", "hours": 8, "reason": "越权调整"}, status=403)
    payload = {k: v for k, v in employee_input.items() if k != "initialPassword"}
    payload["managerId"] = "u2001"
    onboard = prepare(client, "onboard_employee", data=payload, user="u3001")
    assert not app.state.db.one("SELECT id FROM users WHERE username=?", payload["username"])
    confirm(client, onboard, "u3001", status=400)
    confirm(client, onboard, "u3001", data={"initialPassword": employee_input["initialPassword"]})
    assert app.state.db.one("SELECT id FROM users WHERE username=?", payload["username"])
    assert employee_input["initialPassword"] not in json.dumps(app.state.db.all("SELECT * FROM assistant_actions"))
    updated = prepare(client, "update_employee", "u1001", {"leaveApproverId": "u3001"}, "u3001")
    confirm(client, updated, "u3001")
    assert app.state.db.user("u1001")["leaveApproverId"] == "u3001"


def test_chat_materials_and_queries_share_page_permissions(client, app, leave_input):
    leave = create(client, leave_input)
    workspace = chat(client, "打开申请", contextId=leave["id"])
    headers = {**auth_headers(app), "x-filename": "proof.pdf", "content-type": "application/octet-stream"}
    response = client.post(f"/api/agent/workspace/{workspace['id']}/attachments", headers=headers, content=b"%PDF-1.4 fixture")
    assert response.status_code == 200, response.text
    assert len(response.json()["context"]["attachments"]) == 1
    service = app.state.assistant_service
    user = app.state.db.user("u1001")
    for resource, kind in [
        ("ledger", "ledger"),
        ("calendar", "calendar"),
        ("notifications", "notifications"),
        ("organization", "organization"),
    ]:
        assert service.query(user, resource)["kind"] == kind
    api(client, "POST", "/agent/messages", {"message": "查看详情", "contextId": leave["id"]}, "u1002", 403)
    import pytest
    from oa.domain import BusinessError
    with pytest.raises(BusinessError):
        service.query(app.state.db.user("u1002"), "ledger", "u1001")


def test_conversation_and_pending_action_survive_restart(tmp_path, password_hash, leave_input):
    path = tmp_path / "persistent.db"
    first = create_app(path)
    for user in first.state.db.users():
        first.state.auth.set_password(user["id"], password_hash, must_change=False)
    with TestClient(first) as client:
        draft = create(client, leave_input)
        prepared = prepare(client, "submit", draft["id"])
    second = create_app(path)
    with TestClient(second) as client:
        restored = api(client, "GET", "/agent/workspace/" + prepared["id"])
        assert restored["action"]["id"] == prepared["action"]["id"]
        assert restored["messages"]
        assert confirm(client, restored)["context"]["leave"]["status"] == "human_reviewing"


@pytest.mark.parametrize("operation", ["submit", "apply_leave"])
def test_concurrent_confirm_does_not_resubmit_or_hold_transaction(app, leave_input, operation):
    entered, release = asyncio.Event(), asyncio.Event()
    async def reviewer(*args):
        entered.set()
        await release.wait()
        return {"decision": "escalate", "summary": "", "reason": "人工确认", "missingFields": [],
                "riskFlags": [], "policyReferences": [], "confidence": 1, "mode": "deterministic-fallback"}
    app.state.leave_service.reviewer = reviewer
    user = app.state.db.user("u1001")
    leave = app.state.leave_service.create(user, leave_input) if operation == "submit" else None
    conversation = app.state.conversations.begin(user["id"])
    conversation["messages"].append({"role": "user", "content": "提交"})
    app.state.conversations.finish(conversation)
    action = app.state.assistant_service.prepare(conversation, user, operation,
        leave["id"] if leave else None, leave_input if operation == "apply_leave" else {})
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            headers = auth_headers(app)
            path = f"/api/agent/actions/{action['id']}/confirm"
            task = asyncio.create_task(client.post(path, headers=headers))
            await asyncio.wait_for(entered.wait(), 5)
            assert not app.state.db.sql.in_transaction
            duplicate = await client.post(path, headers=headers)
            assert duplicate.status_code == 409
            release.set()
            assert (await task).status_code == 200
    asyncio.run(run())
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1
    leave = app.state.db.list_leaves("u1001")["mine"][0]
    assert len([a for a in app.state.db.actions(leave["id"]) if a["action"] == "submit"]) == 1


@pytest.mark.parametrize("operation", ["create_leave", "apply_leave"])
def test_calendar_changes_invalidate_creation_preview(client, app, leave_input, operation):
    draft = prepare(client, operation, data=leave_input)
    calendar = prepare(client, "save_calendar", data={"day": leave_input["startAt"][:10],
        "isWorkday": False, "name": "假日"}, user="u3001")
    confirm(client, calendar, "u3001")
    confirm(client, draft, status=409)
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_replaced_actions_cannot_execute_and_long_chat_can_confirm(client, app, leave_input):
    first = prepare(client, "create_leave", data=leave_input)
    second = prepare(client, "create_leave", data={**leave_input, "reason": "最新原因"}, conversation=first["id"])
    confirm(client, first, status=409)
    conversation = app.state.conversations.get("u1001", first["id"])
    conversation["messages"] = [{"role": "user", "content": "历史消息"}] * 60
    confirmed = confirm(client, second)
    assert confirmed["context"]["leave"]["reason"] == "最新原因"
    assert len(confirmed["messages"]) < 60
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1


def test_unknown_target_does_not_fall_back_to_selected_request(client, app, leave_input):
    leave = pending(client, leave_input)
    prepare(client, "approve", "missing-request", user="u2001", status=404)
    workspace = prepare(client, "approve", leave["id"], user="u2001")
    assert workspace["action"]["targetId"] == leave["id"]


def test_reason_dates_do_not_change_leave_period(client, leave_input):
    leave = create(client, leave_input)
    workspace = prepare(
        client,
        "edit_leave",
        leave["id"],
        {"reason": "明天下午要到医院检查"},
    )
    workspace = confirm(client, workspace)
    assert workspace["context"]["leave"]["startAt"] == leave["startAt"]
    assert workspace["context"]["leave"]["endAt"] == leave["endAt"]
    assert workspace["context"]["leave"]["reason"] == "明天下午要到医院检查"


def test_model_prepares_action_but_cannot_execute_it(client, app, gateway, leave_input):  # noqa: F811
    steps, requests = gateway
    steps.extend([tool_reply("apply_leave", leave_input),
        structured({"status": "no_action", "message": "已经创建", "missingFields": []})])
    workspace = chat(client, "帮我办理请假")
    assert workspace["action"]["status"] == "pending"
    assert "请核对" in workspace["messages"][-1]["content"]
    assert not app.state.db.list_leaves("u1001")["mine"]
    names = {t["function"]["name"] for t in requests[0]["tools"]}
    assert {"query_oa", "apply_leave", "prepare_oa_action", "get_leave_policy", "get_current_datetime"} <= names
    assert not any("confirm" in n or "execute" in n for n in names)
    workspace = confirm(client, workspace)
    assert workspace["context"]["leave"]["status"] == "human_reviewing"


def test_model_cannot_report_fabricated_execution(client, app, gateway):  # noqa: F811
    steps, _ = gateway
    steps.append(structured({"status": "no_action", "message": "已经批准并完成提交", "missingFields": []}))
    workspace = chat(client, "帮我处理")
    assert workspace["action"] is None
    assert "没有产生业务变更" in workspace["messages"][-1]["content"]
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_restart_recovers_submit_receipt_without_duplicate(tmp_path, password_hash, leave_input):
    path = tmp_path / "interrupted.db"
    first = create_app(path)
    for user in first.state.db.users():
        first.state.auth.set_password(user["id"], password_hash, must_change=False)
    with TestClient(first) as client:
        draft = create(client, leave_input)
        prepared = prepare(client, "submit", draft["id"])
        first.state.db.execute("UPDATE assistant_actions SET status='executing' WHERE id=?", prepared["action"]["id"])
        api(client, "POST", f"/leave-requests/{draft['id']}/submit", {"version": draft["version"]})
    second = create_app(path)
    with TestClient(second) as client:
        workspace = api(client, "GET", "/agent/workspace/" + prepared["id"])
        assert workspace["action"]["status"] == "succeeded"
        confirm(client, workspace)
        assert len([a for a in second.state.db.actions(draft["id"]) if a["action"] == "submit"]) == 1


def test_apply_leave_validation_and_rollback_do_not_leave_drafts(client, app, leave_input, monkeypatch):
    day = leave_input["startAt"][:10]
    app.state.db.execute("INSERT OR REPLACE INTO work_calendar VALUES(?,0,'休息日','u3001',?)", day, now_iso())
    prepare(client, "apply_leave", data=leave_input, status=400)
    assert not app.state.db.all("SELECT id FROM leave_requests")
    app.state.db.execute("DELETE FROM work_calendar WHERE day=?", day)
    workspace = prepare(client, "apply_leave", data=leave_input)
    original = app.state.leave_service.start_submission
    def fail_after_submission(*args):
        original(*args)
        raise BusinessError("测试：提交失败")
    monkeypatch.setattr(app.state.leave_service, "start_submission", fail_after_submission)
    confirm(client, workspace, status=400)
    for table in ("leave_requests", "approval_actions", "approval_steps", "notifications"):
        assert not app.state.db.all("SELECT * FROM " + table)
    assert app.state.assistant_service.action(workspace["action"]["id"], app.state.db.user("u1001"))["status"] == "failed"


def test_apply_leave_requires_fresh_approver_and_user_confirmation(client, app, leave_input):
    workspace = prepare(client, "apply_leave", data=leave_input)
    assert any(f["label"] == "审批人" and "林夏" in f["value"] for f in workspace["action"]["preview"]["fields"])
    chat(client, "好的", workspace["id"], actionId=workspace["action"]["id"])
    assert not app.state.db.list_leaves("u1001")["mine"]
    confirm(client, workspace, user="u1002", status=404)
    app.state.db.execute("UPDATE users SET leave_approver_id='u3001' WHERE id='u1001'")
    confirm(client, workspace, status=409)
    assert not app.state.db.list_leaves("u1001")["mine"]
    updated = prepare(client, "apply_leave", data=leave_input, conversation=workspace["id"])
    assert any(f["label"] == "审批人" and "周宁" in f["value"] for f in updated["action"]["preview"]["fields"])
    api(client, "POST", f"/agent/actions/{updated['action']['id']}/cancel")
    confirm(client, updated, status=409)
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_apply_leave_restart_recovers_committed_submission(tmp_path, password_hash, leave_input):
    async def interrupted(*args):
        raise asyncio.CancelledError()
    path = tmp_path / "apply-interrupted.db"
    first = create_app(path, reviewer=interrupted)
    for user in first.state.db.users():
        first.state.auth.set_password(user["id"], password_hash, must_change=False)
    with TestClient(first) as client:
        prepared = prepare(client, "apply_leave", data=leave_input)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(first.state.assistant_service.confirm(prepared["action"]["id"], first.state.db.user("u1001")))
        action = first.state.assistant_service.action(prepared["action"]["id"], first.state.db.user("u1001"))
        assert action["status"] == "executing" and action["target_id"]
        assert not first.state.db.sql.in_transaction
    second = create_app(path)
    with TestClient(second) as client:
        workspace = api(client, "GET", "/agent/workspace/" + prepared["id"])
        assert workspace["action"]["status"] == "succeeded"
        assert workspace["context"]["leave"]["id"] == action["target_id"]
        assert workspace["context"]["leave"]["status"] == "human_reviewing"
        confirm(client, workspace)
        assert len(second.state.db.list_leaves("u1001")["mine"]) == 1
        assert len([a for a in second.state.db.actions(action["target_id"]) if a["action"] == "submit"]) == 1
