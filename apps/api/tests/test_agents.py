import asyncio
import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from conftest import api, pending
from oa import agents
from oa.domain import BusinessError


def tool_reply(name, data):
    return {
        "content": None,
        "tool_calls": [
            {"id": "call_" + name, "type": "function", "function": {"name": name, "arguments": json.dumps(data)}}
        ],
    }


def structured(data):
    def reply(body):
        name = next(
            item["function"]["name"]
            for item in body["tools"]
            if item["function"]["parameters"].get("properties", {}).get("missingFields")
        )
        return tool_reply(name, data)

    return reply


@pytest.fixture
def gateway(monkeypatch):
    steps, requests, errors = [], [], []

    def respond(request):
        try:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content)
            assert body["model"] == "test-model"
            requests.append(body)
            assert steps, "Unexpected extra model request"
            step = steps.pop(0)
            reply = step(body) if callable(step) else step
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", **reply},
                            "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        except Exception as error:
            errors.append(error)
            return httpx.Response(400, json={"error": {"message": "Test gateway assertion failed"}})

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        agents,
        "create_model",
        lambda: ChatOpenAI(
            api_key="test-key",
            model="test-model",
            base_url="http://mock/v1",
            temperature=0,
            use_responses_api=False,
            max_retries=0,
            http_client=httpx.Client(transport=transport),
            http_async_client=httpx.AsyncClient(transport=transport),
        ),
    )
    yield steps, requests
    assert errors == []
    assert steps == []


def ask(client, message="有哪些请假审批流程", conversation_id=None, user="u1001", status=200):
    data = {"message": message}
    if conversation_id:
        data["conversationId"] = conversation_id
    return api(client, "POST", "/agent/leave/draft", data, user, status)


def test_model_settings_and_compatibility(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODEL", "openai:company-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_DEFAULT_HEADERS_JSON", '{"X-Project":"oa"}')
    settings = agents.model_settings()
    assert settings["model"] == "company-model"
    model = agents.create_model()
    assert model.openai_api_base == "https://example.test/v1"
    assert model.use_responses_api is False
    assert model.default_headers == {"X-Project": "oa"}
    monkeypatch.setenv("OPENAI_DEFAULT_HEADERS_JSON", "[]")
    with pytest.raises(BusinessError):
        agents.model_settings()


@pytest.mark.parametrize("format", ["structured", "json", "fenced", "text"])
def test_compatible_response_formats(client, app, gateway, format):
    steps, requests = gateway
    response = {"status": "needs_information", "message": "请补充原因", "missingFields": ["请假原因"]}
    steps.append(
        structured(response)
        if format == "structured"
        else {
            "content": response["message"]
            if format == "text"
            else ("```json\n" + json.dumps(response) + "\n```")
            if format == "fenced"
            else json.dumps(response)
        }
    )
    value = ask(client)
    assert value["message"] == response["message"]
    assert value["status"] == ("no_action" if format == "text" else "needs_information")
    assert len(requests) == 1
    assert not app.state.db.list_leaves("u1001")["mine"]


@pytest.mark.parametrize("content", ["", "   ", "{broken", "[]", "null"])
def test_empty_invalid_model_response(client, gateway, content):
    gateway[0].append({"content": content})
    assert "答复" in ask(client, status=502)["message"]


@pytest.mark.parametrize("status", ["draft_created", "approval_completed", "rejection_completed"])
def test_fabricated_success_rejected(client, app, gateway, status):
    gateway[0].append(structured({"status": status, "message": "已完成", "missingFields": []}))
    ask(client, status=502)
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_policy_tool_uses_current_rules(client, gateway):
    steps, requests = gateway
    steps.append(tool_reply("get_leave_policy", {}))

    def finish(body):
        text = str(body["messages"])
        assert "无直属领导" in text and "请假审批人" in text
        return {"content": "董事长可以没有直属领导，请假需要指定审批人。"}

    steps.append(finish)
    value = ask(client)
    assert value["status"] == "no_action"
    assert len(requests) == 2


def test_duplicate_parallel_drafts_and_empty_final_response(client, app, gateway, leave_input):
    steps, _ = gateway
    reply = tool_reply("create_leave_draft", leave_input)
    reply["tool_calls"].append({**reply["tool_calls"][0], "id": "call_duplicate"})
    steps.extend([reply, {"content": ""}])
    value = ask(client, "帮我发起请假", status=201)
    assert value["status"] == "draft_created"
    assert len(value["execution"]["toolCalls"]) == 1
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1
    again = ask(client, "再来一次", value["conversation"]["id"])
    assert again["leave"]["id"] == value["leave"]["id"]


def test_assistant_cannot_execute_approvals(client, app, gateway, leave_input, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    first = pending(client, leave_input)
    second = pending(client, leave_input, "u1002")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    steps, _ = gateway
    steps.append(tool_reply("list_pending_leave_approvals", {}))
    approve = tool_reply("approve_leave_request", {"requestId": first["id"], "reason": "同意申请"})
    approve["tool_calls"].append(
        {
            **tool_reply("approve_leave_request", {"requestId": second["id"], "reason": "同意申请"})["tool_calls"][0],
            "id": "call_second",
        }
    )
    steps.extend([approve, {"content": ""}])
    value = ask(client, "通过所有待审批申请", user="u2001")
    assert value["status"] == "no_action"
    assert not value.get("approvedLeaves")
    assert not app.state.db.history("u2001")
    assert app.state.db.remaining("u1001", "sick") == 80
    assert all(app.state.db.leave(item["id"])["status"] == "human_reviewing" for item in (first, second))


def test_empty_final_query_uses_actual_results(client, gateway):
    gateway[0].extend([tool_reply("list_pending_leave_approvals", {}), {"content": ""}])
    value = ask(client, "查询待审批", user="u2001")
    assert value["status"] == "no_action"
    assert "没有" in value["message"]


def test_model_context_corrections_and_failed_turn_not_saved(client, gateway):
    steps, requests = gateway
    steps.append(structured({"status": "needs_information", "message": "请补充原因", "missingFields": ["请假原因"]}))
    first = ask(client, "9月17号请假")
    key = first["conversation"]["id"]
    steps.append({"content": ""})
    ask(client, "失败轮次", key, status=502)
    steps.append(structured({"status": "needs_information", "message": "收到日期更正", "missingFields": ["请假原因"]}))
    ask(client, "改成9月18号", key)
    history = requests[-1]["messages"]
    assert any(turn["content"] == "9月17号请假" for turn in history)
    assert not any(turn["content"] == "失败轮次" for turn in history)
    assert "默认该日全天" in str(history)
    api(client, "GET", f"/agent/conversations/{key}", user="u1002", status=404)


def test_local_conversation_whole_day_and_completed_task_isolation(client, app):
    first = ask(client, "9月17号")
    assert first["missingFields"] == ["请假原因"]
    key = first["conversation"]["id"]
    value = ask(client, "头疼", key, status=201)
    assert value["leave"]["durationHours"] == 7.5
    assert value["leave"]["leaveType"] == "sick"
    ask(client, "重新创建", key)
    assert len(app.state.db.list_leaves("u1001")["mine"]) == 1
    api(client, "DELETE", f"/leave-requests/{value['leave']['id']}", status=204)
    assert ask(client, "好的", key)["status"] == "no_action"
    assert "请假日期" in ask(client, "因为有事", key)["missingFields"]
    assert "流程" in ask(client)["message"]
    assert "人工确认" in ask(client, "批准全部申请")["message"]


async def test_reviewer_failure_uses_local_rules(monkeypatch, app, leave_input):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fail(*args):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(agents, "build_agent", fail)
    leave = app.state.leave_service.create(app.state.db.user("u1001"), leave_input)
    result = await agents.review_leave(leave, True, False)
    assert result["mode"] == "deterministic-fallback" and result["decision"] == "escalate"
    app.state.db.close()


async def test_simultaneous_conversation_requests_are_rejected(tmp_path, monkeypatch):
    from oa.app import create_app

    started, finish = asyncio.Event(), asyncio.Event()

    async def assistant(message, user, api, history):
        started.set()
        await finish.wait()
        return agents.result_payload("no_action", "收到", "deterministic-fallback")

    app = create_app(tmp_path / "busy.db", assistant=assistant)
    from conftest import auth_headers

    store = app.state.conversations
    conversation = store.begin("u1001")
    conversation["messages"].append({"role": "user", "content": "已有消息"})
    store.finish(conversation)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        client.headers.update(auth_headers(app))
        data = {"message": "继续", "conversationId": conversation["id"]}
        task = asyncio.create_task(client.post("/api/agent/leave/draft", json=data))
        await asyncio.wait_for(started.wait(), 5)
        assert (await client.post("/api/agent/leave/draft", json=data)).status_code == 409
        finish.set()
        assert (await task).status_code == 200
    app.state.db.close()
