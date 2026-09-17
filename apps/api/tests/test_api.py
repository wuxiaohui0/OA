import asyncio
import sqlite3

import httpx
import pytest

from conftest import api, auth_headers, employee_update, pending
from oa.agents import deterministic_review


def test_bootstrap_auth_and_openapi(client):
    value = api(client, "GET", "/bootstrap")
    assert value["currentUser"]["name"] == "陈默"
    assert value["organization"]["manager"]["id"] == "u2001"
    assert value["balances"][0]["remainingHours"] == 64
    api(client, "GET", "/bootstrap", user="missing", status=401)
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/leave-requests/approval-history" in schema["paths"]
    assert "x-user-id" not in str(value["agentConfig"])


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_history_survives_completion_and_soft_delete(client, app, leave_input, decision):
    leave = pending(client, leave_input)
    assert leave["currentApproverId"] == "u2001"
    path = f"/leave-requests/{leave['id']}"
    api(client, "POST", path + "/" + decision, {"reason": "处理意见"}, user="u1002", status=403)
    result = api(client, "POST", path + "/" + decision, {"reason": "处理意见"}, user="u2001")
    status = "approved" if decision == "approve" else "rejected"
    assert result["leave"]["status"] == status
    assert result["approvalSteps"][0]["comment"] == "处理意见"
    assert api(client, "GET", "/leave-requests/pending-approvals", user="u2001")["items"] == []
    history = api(client, "GET", "/bootstrap", user="u2001")["approvalHistory"]
    assert history[0]["id"] == leave["id"]
    assert history[0]["reviewDecision"] == status
    assert history[0]["reviewComment"] == "处理意见"
    assert history[0]["reviewedAt"]
    if decision == "reject":
        api(client, "DELETE", path, status=204)
        assert api(client, "GET", "/bootstrap")["mine"] == []
    else:
        api(client, "DELETE", path, status=400)
    assert api(client, "GET", path, user="u2001")["actions"][-1]["action"] in (decision, "delete")
    api(client, "GET", path, user="u1002", status=403)
    assert api(client, "GET", "/leave-requests/approval-history", user="u1002")["items"] == []
    assert len(api(client, "GET", "/leave-requests/approval-history", user="u2001")["items"]) == 1
    api(client, "POST", path + "/" + decision, user="u2001", status=400 if decision == "approve" else 404)
    assert app.state.db.remaining("u1001", "sick") == (72.5 if decision == "approve" else 80)


def test_ai_advice_never_approves_without_human(client, app, leave_input):
    data = {**leave_input, "leaveType": "annual", "startAt": leave_input["startAt"].replace("09:00", "13:30")}
    leave = pending(client, data)
    assert leave["status"] == "human_reviewing"
    assert app.state.db.remaining("u1001", "annual") == 64
    assert app.state.db.history("u2001") == []
    api(client, "POST", f"/leave-requests/{leave['id']}/approve", {"version": leave["version"]}, user="u2001")
    assert app.state.db.remaining("u1001", "annual") == 59.5
    assert app.state.db.actions(leave["id"])[-1]["actorType"] == "human"


@pytest.mark.parametrize(
    "change", [{"reason": ""}, {"leaveType": "other"}, {"startAt": "invalid"}, {"endAt": "2000-01-01"}]
)
def test_invalid_drafts(client, app, leave_input, change):
    api(client, "POST", "/leave-requests", {**leave_input, **change}, status=400)
    assert not app.state.db.list_leaves("u1001")["mine"]


def test_overlap_and_insufficient_balance_route_to_human(client, app, leave_input):
    data = {**leave_input, "leaveType": "annual", "startAt": leave_input["startAt"].replace("09:00", "13:30")}
    first = pending(client, data)
    api(client, "POST", f"/leave-requests/{first['id']}/approve", user="u2001")
    assert pending(client, data)["status"] == "human_reviewing"
    app.state.db.execute("UPDATE leave_balances SET used_hours=total_hours WHERE user_id='u1002'")
    assert pending(client, leave_input, "u1002")["status"] == "human_reviewing"


def test_draft_deletion_withdrawal_and_rejection_reason(client, app, leave_input):
    leave = pending(client, leave_input)
    path = f"/leave-requests/{leave['id']}"
    api(client, "POST", path + "/reject", user="u2001", status=400)
    api(client, "DELETE", path, user="u1002", status=403)
    api(client, "DELETE", path, status=204)
    assert app.state.db.leave(leave["id"])["status"] == "withdrawn"
    assert app.state.db.steps(leave["id"])[0]["status"] == "skipped"
    assert app.state.db.pending("u2001") == []
    api(client, "POST", path + "/submit", status=404)


def test_approval_transaction_rolls_back_on_balance_failure(client, app, leave_input):
    leave = pending(client, leave_input)
    db = app.state.db
    db.execute(
        "CREATE TRIGGER fail_balance BEFORE UPDATE ON leave_balances BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        client.post(f"/api/leave-requests/{leave['id']}/approve", headers=auth_headers(app, "u2001"))
    assert db.leave(leave["id"])["status"] == "human_reviewing"
    assert db.steps(leave["id"])[0]["status"] == "pending"
    assert db.history("u2001") == []
    assert db.remaining("u1001", "sick") == 80


async def test_withdraw_during_model_review_does_not_resurrect(app, leave_input):
    started, finish = asyncio.Event(), asyncio.Event()

    async def reviewer(leave, sufficient, overlap):
        started.set()
        await finish.wait()
        return deterministic_review(leave, sufficient, overlap)

    app.state.leave_service.reviewer = reviewer
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        client.headers.update(auth_headers(app))
        leave = (await client.post("/api/leave-requests", json=leave_input)).json()["leave"]
        path = f"/api/leave-requests/{leave['id']}"
        task = asyncio.create_task(client.post(path + "/submit"))
        await asyncio.wait_for(started.wait(), 5)
        assert (await client.delete(path)).status_code == 204
        finish.set()
        assert (await task).json()["leave"]["status"] == "withdrawn"
    assert not app.state.db.history("u2001")
    assert app.state.db.remaining("u1001", "sick") == 80
    app.state.db.close()


def test_transfer_keeps_submission_snapshot(client, app, leave_input):
    old_department = app.state.db.user("u1001")["department"]
    leave = pending(client, leave_input)
    employee = app.state.db.user("u1001")
    changed = employee_update(employee, departmentId=app.state.db.user("u3001")["departmentId"], managerId="u3001")
    api(client, "PUT", "/employees/u1001", changed, "u3001")
    assert app.state.db.leave(leave["id"])["department"] == old_department
    assert app.state.db.leave(leave["id"])["currentApproverId"] == "u2001"
    second = pending(client, leave_input)
    assert second["currentApproverId"] == "u3001"
