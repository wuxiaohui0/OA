import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from conftest import api, auth_headers, create, pending
from oa.agents import deterministic_review
from oa.db import Database


def path(leave):
    return f"/leave-requests/{leave['id']}"


def approve(client, leave, user="u2001"):
    return api(client, "POST", path(leave) + "/approve", {"version": leave["version"]}, user)["leave"]


def cancellation(client, leave, start=None, end=None):
    return api(client, "POST", path(leave) + "/cancellations", {
        "version": leave["version"], "startAt": start or leave["startAt"],
        "endAt": end or leave["endAt"], "reason": "行程变更申请销假",
    }, status=201)


@pytest.mark.parametrize("action", ["request-information", "reject", "withdraw"])
def test_edit_resubmit_preserves_history_and_version(client, app, leave_input, action):
    leave = pending(client, leave_input)
    actor = "u1001" if action == "withdraw" else "u2001"
    returned = api(client, "POST", path(leave) + "/" + action, {"version": leave["version"], "reason": "补充详细说明"}, actor)["leave"]
    data = {**leave_input, "reason": "补充原因与工作交接说明", "version": returned["version"]}
    api(client, "PUT", path(leave), data, "u1002", 403)
    api(client, "PUT", path(leave), {**data, "version": leave["version"]}, status=409)
    edited = api(client, "PUT", path(leave), data)
    assert edited["leave"]["status"] == "draft" and edited["review"] is None
    assert any('补充原因' in a["reason"] for a in edited["actions"] if a["action"] == "edit")
    resubmitted = api(client, "POST", path(leave) + "/submit")["leave"]
    final = approve(client, resubmitted)
    assert final["status"] == "approved"
    assert [s["status"] for s in app.state.db.steps(leave["id"])] == ["rejected" if action == "reject" else "skipped", "approved"]
    assert app.state.db.remaining("u1001", "sick") == 72.5
    assert len(app.state.db.all("SELECT id FROM balance_ledger WHERE request_id=?", leave["id"])) == 1


def test_work_calendar_holiday_weekend_and_stale_draft(client, app, leave_input):
    leave = create(client, leave_input)
    day = leave_input["startAt"][:10]
    setting = {"day": day, "isWorkday": False, "name": "公司休息日"}
    api(client, "PUT", "/work-calendar", setting, status=403)
    api(client, "PUT", "/work-calendar", setting, "u3001")
    api(client, "POST", path(leave) + "/submit", status=409)
    revised = api(client, "PUT", path(leave), {**leave_input, "version": leave["version"]})["leave"]
    assert revised["durationHours"] == 0
    api(client, "POST", path(leave) + "/submit", status=400)
    saturday = datetime.fromisoformat(day) + timedelta(days=(5 - datetime.fromisoformat(day).weekday()) % 7)
    weekend = saturday.date().isoformat()
    weekend_input = {**leave_input, "startAt": f"{weekend}T09:00+08:00", "endAt": f"{weekend}T18:00+08:00"}
    assert create(client, weekend_input)["durationHours"] == 0
    api(client, "PUT", "/work-calendar", {"day": weekend, "isWorkday": True, "name": "调休补班"}, "u3001")
    assert create(client, weekend_input)["durationHours"] == 7.5
    api(client, "DELETE", "/work-calendar/" + weekend, user="u3001", status=204)
    assert create(client, weekend_input)["durationHours"] == 0
    assert app.state.db.one("SELECT id FROM organization_actions WHERE action='update_calendar'")


def test_partial_cancellation_uses_snapshot_and_releases_overlap(client, app, leave_input):
    leave = approve(client, pending(client, leave_input))
    day = leave_input["startAt"][:10]
    api(client, "PUT", "/work-calendar", {"day": day, "isWorkday": False, "name": "后续日历修订"}, "u3001")
    detail = cancellation(client, leave, end=f"{day}T12:00+08:00")
    cancel = detail["cancellations"][0]
    assert cancel["hours"] == 3
    api(client, "POST", f"/cancellations/{cancel['id']}/approve", {"reason": "同意返还"}, status=403)
    approved = api(client, "POST", f"/cancellations/{cancel['id']}/approve", {"reason": "同意返还"}, "u2001")
    assert approved["leave"]["status"] == "approved"
    assert app.state.db.remaining("u1001", "sick") == 75.5
    api(client, "POST", f"/cancellations/{cancel['id']}/approve", {"reason": "重复点击"}, "u2001", 409)
    api(client, "DELETE", "/work-calendar/" + day, user="u3001", status=204)
    replacement = pending(client, {**leave_input, "endAt": f"{day}T12:00+08:00"})
    assert approve(client, replacement)["status"] == "approved"
    detail = cancellation(client, approved["leave"], start=f"{day}T13:30+08:00")
    final = api(client, "POST", f"/cancellations/{detail['cancellations'][0]['id']}/approve", {"reason": "同意返还"}, "u2001")
    assert final["leave"]["status"] == "cancelled"
    assert app.state.db.remaining("u1001", "sick") == 77
    ledger = api(client, "GET", "/balance-ledger/u1001")
    entries = [e for e in ledger["entries"] if e["requestId"] == leave["id"]]
    assert sum(e["usedDelta"] for e in entries) == 0


def test_fractional_cancellations_refund_exactly_once(client, app, leave_input):
    day = leave_input["startAt"][:10]
    data = {**leave_input, "endAt": f"{day}T09:03+08:00"}
    leave = approve(client, pending(client, data))
    for minute in range(3):
        detail = cancellation(client, leave, f"{day}T09:0{minute}+08:00", f"{day}T09:0{minute + 1}+08:00")
        leave = api(client, "POST", f"/cancellations/{detail['cancellations'][0]['id']}/approve", {"reason": "按实际返还"}, "u2001")["leave"]
    assert leave["status"] == "cancelled"
    assert app.state.db.remaining("u1001", "sick") == 80


@pytest.mark.parametrize("action", ["reject", "withdraw"])
def test_cancel_reject_withdraw_overlap_and_retry(client, app, leave_input, action):
    leave = approve(client, pending(client, leave_input))
    detail = cancellation(client, leave)
    body = {"version": detail["leave"]["version"], "reason": "重复销假申请", "startAt": leave["startAt"], "endAt": leave["endAt"]}
    api(client, "POST", path(leave) + "/cancellations", body, status=409)
    cancel = detail["cancellations"][0]
    detail = api(client, "POST", f"/cancellations/{cancel['id']}/{action}", {"reason": "行程再次调整"}, "u1001" if action == "withdraw" else "u2001")
    assert app.state.db.remaining("u1001", "sick") == 72.5
    assert len(cancellation(client, detail["leave"])["cancellations"]) == 2


def test_approval_guards_roll_back_and_legacy_cannot_refund(client, app, leave_input):
    first = pending(client, leave_input)
    second = pending(client, leave_input)
    api(client, "POST", path(first) + "/approve", user="u2001", status=409)
    api(client, "POST", path(second) + "/withdraw", {"reason": "重复申请撤回", "version": second["version"]})
    app.state.db.execute("UPDATE leave_balances SET total_hours=0 WHERE user_id='u1001' AND leave_type='sick'")
    api(client, "POST", path(first) + "/approve", user="u2001", status=409)
    assert app.state.db.leave(first["id"])["status"] == "human_reviewing"
    assert app.state.db.steps(first["id"])[0]["status"] == "pending"
    assert not app.state.db.one("SELECT id FROM balance_ledger WHERE request_id=?", first["id"])
    app.state.db.execute("UPDATE leave_balances SET total_hours=80 WHERE user_id='u1001' AND leave_type='sick'")
    leave = approve(client, first)
    app.state.db.execute("DELETE FROM balance_ledger WHERE request_id=?", first["id"])
    api(client, "POST", path(first) + "/cancellations", {"version": leave["version"], "reason": "历史单据销假", "startAt": leave["startAt"], "endAt": leave["endAt"]}, status=409)


def test_ledger_adjustment_idempotency_authorization_and_migration(client, app):
    body = {"userId": "u1001", "leaveType": "annual", "hours": 3, "reason": "年度额度核定", "operationId": str(uuid4())}
    api(client, "POST", "/balance-adjustments", body, status=403)
    for _ in range(2):
        result = api(client, "POST", "/balance-adjustments", body, "u3001")
        assert result["balances"][0]["totalHours"] == 83
    api(client, "POST", "/balance-adjustments", {**body, "hours": 4}, "u3001", 409)
    api(client, "POST", "/balance-adjustments", {**body, "operationId": str(uuid4()), "hours": -80}, "u3001", 409)
    api(client, "GET", "/balance-ledger/u1002", status=403)
    assert len(result["entries"]) == 4
    app.state.db.migrate()
    assert len(api(client, "GET", "/balance-ledger/u1001")["entries"]) == 4
    response = client.get("/api/balance-ledger/u1001/export", headers=auth_headers(app))
    assert response.status_code == 200 and response.content.startswith(b"\xef\xbb\xbf")


def test_handoff_keeps_steps_and_revokes_old_approval(client, app, leave_input):
    leave = pending(client, leave_input)
    transfer = {"version": leave["version"], "approverId": "u3001", "reason": "休假期间交接"}
    api(client, "POST", path(leave) + "/transfer", transfer, "u1002", 403)
    for target in ("u1001", "u2001", "u1002"):
        response = client.post("/api" + path(leave) + "/transfer", json={**transfer, "approverId": target}, headers=auth_headers(app, "u2001"))
        assert response.status_code in (400, 403)
    detail = api(client, "POST", path(leave) + "/transfer", transfer, "u2001")
    api(client, "POST", path(leave) + "/approve", user="u2001", status=403)
    api(client, "POST", path(leave) + "/approve", {"version": leave["version"]}, "u3001", 409)
    assert [s["status"] for s in detail["approvalSteps"]] == ["skipped", "pending"]
    assert api(client, "GET", path(leave), user="u2001")["leave"]["currentApproverId"] == "u3001"
    assert api(client, "GET", "/bootstrap", user="u2001")["approvalHistory"][0]["reviewDecision"] == "transferred"
    approved = approve(client, detail["leave"], "u3001")
    detail = cancellation(client, approved)
    key = detail["cancellations"][0]["id"]
    api(client, "POST", f"/cancellations/{key}/transfer", {**transfer, "version": detail["leave"]["version"]}, "u3001")
    api(client, "POST", f"/cancellations/{key}/approve", {"reason": "旧审批人操作"}, "u2001", 403)
    result = api(client, "POST", f"/cancellations/{key}/approve", {"reason": "接手处理销假"}, "u3001")
    assert result["leave"]["status"] == "cancelled"
    assert api(client, "GET", "/bootstrap", user="u3001")["approvalHistory"][0]["reviewDecision"] == "cancellation_approved"
    assert not api(client, "GET", "/bootstrap", user="u3001")["cancellationInbox"]


def test_material_permissions_validation_and_evidence_retention(client, app, leave_input):
    leave = create(client, leave_input)
    def upload(content, name="proof.pdf", user="u1001"):
        return client.post("/api" + path(leave) + "/attachments", content=content, headers={**auth_headers(app, user), "x-filename": name})
    assert upload(b"%PDF-1.4 test", user="u1002").status_code == 403
    assert upload(b"not-pdf").status_code == 400
    assert upload(b"%PDF-1.4 /JavaScript").status_code == 400
    assert upload(b"%PDF-" + b"x" * (5 * 1024 * 1024)).status_code == 413
    assert upload(b"%PDF-1.4", "%0Dmalicious.pdf").status_code == 400
    response = upload(b"%PDF-1.4 sample")
    assert response.status_code == 201
    key = response.json()["attachments"][0]["id"]
    api(client, "DELETE", f"/attachments/{key}", user="u1002", status=403)
    api(client, "DELETE", f"/attachments/{key}")
    key = upload(b"%PDF-1.4 sample").json()["attachments"][0]["id"]
    api(client, "POST", path(leave) + "/submit")
    assert upload(b"%PDF-1.4").status_code == 400
    for user, expected in (("u1001", 200), ("u2001", 200), ("u3001", 200), ("u1002", 403)):
        download = client.get(f"/api/attachments/{key}", headers=auth_headers(app, user))
        assert download.status_code == expected
        if expected == 200:
            assert download.content == b"%PDF-1.4 sample" and download.headers["x-content-type-options"] == "nosniff"
    leave = app.state.db.leave(leave["id"])
    returned = api(client, "POST", path(leave) + "/request-information", {"version": leave["version"], "reason": "补充材料说明"}, "u2001")["leave"]
    api(client, "PUT", path(leave), {**leave_input, "version": returned["version"]})
    api(client, "DELETE", f"/attachments/{key}", status=409)


def test_notifications_reminders_and_overdue_dedup(client, app, leave_input):
    leave = pending(client, leave_input)
    api(client, "POST", path(leave) + "/remind", user="u1002", status=403)
    api(client, "POST", path(leave) + "/remind", status=204)
    api(client, "POST", path(leave) + "/remind", status=429)
    app.state.db.execute("UPDATE approval_steps SET created_at='2000-01-01T00:00:00.000Z' WHERE request_id=?", leave["id"])
    app.state.pilot.maintenance()
    app.state.pilot.maintenance()
    notifications = api(client, "GET", "/notifications", user="u2001")
    assert len(notifications["items"]) == 3
    key = notifications["items"][0]["id"]
    api(client, "POST", f"/notifications/{key}/read", status=404)
    api(client, "POST", f"/notifications/{key}/read", user="u2001", status=204)
    assert api(client, "GET", "/notifications", user="u2001")["unread"] == 2
    api(client, "POST", "/notifications/read", user="u2001", status=204)
    assert api(client, "GET", "/bootstrap", user="u2001")["unreadNotifications"] == 0


@pytest.mark.parametrize("status", ["submitted", "validating", "agent_reviewing"])
def test_restart_recovers_pending_ai_without_debit(app, leave_input, status, tmp_path):
    service, db = app.state.leave_service, app.state.db
    leave = service.create(db.user("u1001"), leave_input)
    db.execute("UPDATE leave_requests SET status=?,submission_manager_id='u2001' WHERE id=?", status, leave["id"])
    db.close()
    from oa.app import create_app
    reopened = create_app(tmp_path / "test.db")
    reopened.state.pilot.maintenance(recover=True)
    reopened.state.pilot.maintenance(recover=True)
    assert reopened.state.db.leave(leave["id"])["status"] == "human_reviewing"
    assert len(reopened.state.db.steps(leave["id"])) == 1
    assert reopened.state.db.remaining("u1001", "sick") == 80
    reopened.state.db.close()
    db = Database(tmp_path / "test.db")
    assert len(db.all("SELECT * FROM balance_ledger WHERE entry_key LIKE 'opening:%'")) == 12
    db.close()


async def test_old_ai_result_cannot_overwrite_new_submission(app, leave_input):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    async def review(leave, sufficient, overlap):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            result = deterministic_review(leave, sufficient, overlap)
            result["reason"] = "旧版本审核结果"
            return result
        raise RuntimeError("model offline")
    app.state.leave_service.reviewer = review
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=auth_headers(app)) as client:
        leave = (await client.post("/api/leave-requests", json=leave_input)).json()["leave"]
        endpoint = "/api" + path(leave)
        task = asyncio.create_task(client.post(endpoint + "/submit"))
        await asyncio.wait_for(started.wait(), 5)
        current = app.state.db.leave(leave["id"])
        returned = (await client.post(endpoint + "/withdraw", json={"version": current["version"], "reason": "修改后重提"})).json()["leave"]
        response = await client.put(endpoint, json={**leave_input, "reason": "新版本原因", "version": returned["version"]})
        assert response.status_code == 200
        response = await client.post(endpoint + "/submit")
        assert response.json()["leave"]["status"] == "human_reviewing"
        finish.set()
        assert (await task).json()["leave"]["status"] == "human_reviewing"
        assert len(app.state.db.all("SELECT id FROM agent_reviews WHERE request_id=?", leave["id"])) == 1
        assert "旧版本" not in app.state.db.leave(leave["id"])["agentReason"]
    app.state.db.close()
