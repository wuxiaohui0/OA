import asyncio

import httpx
import pytest

from conftest import auth_headers
from oa import assistant_agent
from oa.run_control import AgentRunRegistry


@pytest.mark.asyncio
async def test_run_registry_cancels_only_the_owned_active_task():
    registry = AgentRunRegistry()
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    control = registry.start("alice", "conversation", task)
    await started.wait()
    assert registry.request_pause("bob", "conversation") is None
    assert registry.request_pause("alice", "conversation") is control
    with pytest.raises(asyncio.CancelledError):
        await task
    registry.finish(control)
    assert control.stopped.is_set()
    assert registry.request_pause("alice", "conversation") is None


@pytest.mark.asyncio
async def test_pause_endpoint_stops_run_and_continue_replays_safely(app, monkeypatch):
    started = asyncio.Event()
    calls = []

    async def controlled_respond(message, *_args, **_kwargs):
        calls.append(message)
        if len(calls) == 1:
            started.set()
            await asyncio.Event().wait()
        return "已从暂停点继续处理。"

    monkeypatch.setattr(assistant_agent, "respond", controlled_respond)
    headers = auth_headers(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/agent/workspace", headers=headers)
        assert created.status_code == 201
        workspace = created.json()
        assert workspace["runStatus"] == "idle"

        running = asyncio.create_task(client.post(
            "/api/agent/messages",
            headers=headers,
            json={"message": "分析一项复杂任务", "conversationId": workspace["id"]},
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        paused = await client.post(
            f"/api/agent/workspace/{workspace['id']}/pause", headers=headers
        )
        original = await asyncio.wait_for(running, timeout=2)

        assert paused.status_code == 200 and original.status_code == 200
        paused_workspace = paused.json()
        assert paused_workspace["runStatus"] == "paused"
        assert paused_workspace["busy"] is False
        assert paused_workspace["messages"][-2]["content"] == "分析一项复杂任务"
        assert "已暂停当前处理" in paused_workspace["messages"][-1]["content"]

        resumed = await client.post(
            "/api/agent/messages",
            headers=headers,
            json={"message": "继续", "conversationId": workspace["id"]},
        )
        assert resumed.status_code == 200
        resumed_workspace = resumed.json()["conversation"]
        assert resumed_workspace["runStatus"] == "completed"
        assert resumed_workspace["messages"][-2]["content"] == "继续"
        assert resumed_workspace["messages"][-1]["content"] == "已从暂停点继续处理。"
        assert "继续处理暂停前的请求" in calls[1]
        assert "分析一项复杂任务" in calls[1]
