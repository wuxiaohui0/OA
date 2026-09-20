from datetime import datetime, timedelta

import pytest

from conftest import api
from oa.domain import SHANGHAI
from test_agents import gateway, structured, tool_reply  # noqa: F401


def prepare(client, operation, data=None, target=None, conversation=None, user="u1001", status=200):
    if conversation is None:
        conversation = api(client, "POST", "/agent/workspace", user=user, status=201)["id"]
    return api(
        client,
        "POST",
        "/agent/prepare",
        {
            "conversationId": conversation,
            "operation": operation,
            "targetId": target,
            "data": data or {},
        },
        user=user,
        status=status,
    )


def confirm(client, workspace, user="u1001", status=200):
    return api(
        client,
        "POST",
        f"/agent/actions/{workspace['action']['id']}/confirm",
        {},
        user=user,
        status=status,
    )


def travel_data():
    return {
        "destination": "上海",
        "purpose": "客户现场交付",
        "startAt": "2026-10-12T09:00:00+08:00",
        "endAt": "2026-10-14T18:00:00+08:00",
        "travelerIds": [],
        "transportStandard": "high_speed",
        "accommodationStandard": "standard",
    }


def procurement_data():
    return {
        "title": "研发显示器",
        "purpose": "研发团队项目开发使用",
        "items": [{"name": "显示器", "quantity": 2, "unitPrice": 1200}],
        "budget": 3000,
        "currency": "CNY",
        "supplier": "办公用品供应商",
        "needBy": (datetime.now(SHANGHAI).date() + timedelta(days=14)).isoformat(),
    }


def overtime_data():
    start = datetime.now(SHANGHAI).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    return {
        "startAt": start.isoformat(),
        "endAt": (start + timedelta(hours=2)).isoformat(),
        "reason": "版本发布支持",
        "compensationType": "comp_leave",
    }


def test_employee_catalog_binds_business_request_operations(client):
    operations = {item["operation"] for item in api(client, "GET", "/agent/catalog")["items"]}
    assert {
        "create_travel", "submit_travel", "withdraw_travel",
        "create_procurement", "submit_procurement", "withdraw_procurement",
        "create_overtime", "submit_overtime", "withdraw_overtime",
        "create_comp_time", "submit_comp_time", "withdraw_comp_time",
    } <= operations


def test_assistant_travel_prepare_confirm_and_submit(client, app):
    workspace = prepare(client, "create_travel", travel_data())
    assert workspace["action"]["status"] == "pending"
    assert not app.state.db.all("SELECT id FROM travel_requests")

    workspace = confirm(client, workspace)
    travel = app.state.assistant_service.travel.get(workspace["action"]["result"]["travel"]["id"])
    assert travel["status"] == "draft"

    submitted = prepare(client, "submit_travel", target=travel["id"], conversation=workspace["id"])
    assert travel["status"] == "draft"
    confirm(client, submitted)
    assert app.state.assistant_service.travel.get(travel["id"])["status"] == "human_reviewing"


def test_assistant_procurement_overtime_and_comp_time_confirm(client, app):
    procurement = confirm(client, prepare(client, "create_procurement", procurement_data()))
    procurement_id = procurement["action"]["result"]["procurement"]["id"]
    assert app.state.assistant_service.procurement.get(procurement_id)["status"] == "draft"

    overtime = confirm(client, prepare(client, "create_overtime", overtime_data()))
    overtime_id = overtime["action"]["result"]["overtime"]["id"]
    assert app.state.assistant_service.overtime.get_overtime(overtime_id)["status"] == "draft"

    comp_time = confirm(
        client,
        prepare(
            client,
            "create_comp_time",
            {"date": "2026-10-10", "hours": 1, "reason": "个人事务"},
        ),
    )
    comp_time_id = comp_time["action"]["result"]["compTime"]["id"]
    assert app.state.assistant_service.overtime.get_comp_time(comp_time_id)["status"] == "draft"


def test_assistant_business_request_isolation(client, app):
    workspace = confirm(client, prepare(client, "create_travel", travel_data()))
    travel_id = workspace["action"]["result"]["travel"]["id"]
    prepare(client, "submit_travel", target=travel_id, user="u1002", status=403)
    assert app.state.assistant_service.travel.get(travel_id)["status"] == "draft"


@pytest.mark.parametrize(("kind", "result_key", "data_factory"), [
    ("travel", "travel", travel_data),
    ("procurement", "procurement", procurement_data),
    ("overtime", "overtime", overtime_data),
    ("comp_time", "compTime", lambda: {"date": "2026-10-10", "hours": 1, "reason": "个人事务"}),
])
def test_model_business_tools_require_confirmation_and_can_submit_receipt(
    client, app, gateway, kind, result_key, data_factory,  # noqa: F811
):
    steps, requests = gateway
    table = kind + "_requests"

    def create_from_catalog(body):
        prompt = body["messages"][0]["content"]
        assert f"create_{kind}" in prompt and f"submit_{kind}" in prompt
        tools = {entry["function"]["name"] for entry in body["tools"]}
        assert "prepare_oa_action" in tools
        assert "prepare_approval_action" not in tools
        assert not any("confirm" in name for name in tools)
        return tool_reply("prepare_oa_action", {"operation": f"create_{kind}", "data": data_factory()})

    steps.extend([create_from_catalog, {"content": "请核对申请预览。"}])
    workspace = api(client, "POST", "/agent/messages", {"message": "帮我发起申请"})["conversation"]
    assert workspace["action"]["operation"] == f"create_{kind}"
    assert workspace["action"]["status"] == "pending"
    assert not app.state.db.all(f"SELECT id FROM {table}")

    confirmed = confirm(client, workspace)
    request_id = confirmed["action"]["result"][result_key]["id"]
    assert request_id in confirmed["messages"][-1]["content"]
    repeated = confirm(client, workspace)
    assert repeated["action"]["result"][result_key]["id"] == request_id
    assert len(app.state.db.all(f"SELECT id FROM {table}")) == 1

    def submit_from_receipt(body):
        assert request_id in str(body["messages"])
        return tool_reply("prepare_oa_action", {"operation": f"submit_{kind}", "target_id": request_id})

    steps.extend([submit_from_receipt, {"content": "请核对提交审批预览。"}])
    submission = api(client, "POST", "/agent/messages", {
        "message": "提交刚才的申请", "conversationId": workspace["id"],
    })["conversation"]
    assert submission["action"]["status"] == "pending"
    assert app.state.db.one(f"SELECT status FROM {table} WHERE id=?", request_id)["status"] == "draft"
    confirm(client, submission)
    assert app.state.db.one(f"SELECT status FROM {table} WHERE id=?", request_id)["status"] == "human_reviewing"
    assert len(requests) == 4


def test_incomplete_travel_prompt_uses_current_capabilities_and_waits_for_details(client, app, gateway):  # noqa: F811
    def clarify_travel(body):
        prompt = body["messages"][0]["content"]
        assert "create_travel" in prompt and "destination" in prompt and "endAt" in prompt
        assert "历史对话中的能力说明可能已过时" in prompt
        return structured({
            "status": "needs_information",
            "message": "请补充出差地点、返程时间和出差事由。",
            "missingFields": ["出差地点", "返程时间", "出差事由"],
        })(body)

    gateway[0].append(clarify_travel)
    workspace = api(client, "POST", "/agent/messages", {"message": "帮我发一个出差申请，后天"})["conversation"]
    assert workspace["action"] is None
    assert workspace["clarification"]["missingFields"] == ["出差地点", "返程时间", "出差事由"]
    assert not app.state.db.all("SELECT id FROM travel_requests")
