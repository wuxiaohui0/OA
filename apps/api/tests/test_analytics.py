from conftest import api, pending


def test_approval_analytics_is_admin_only(client, leave_input):
    value = api(client, "GET", "/analytics/approval", user="u3001")
    assert value["scope"] == "organization"
    assert set(value["summary"]) >= {"total", "pending", "approvalRate", "overdue", "aiReviewed"}
    assert len(value["trend"]) == 6
    api(client, "GET", "/analytics/approval", user="u1001", status=403)


def test_approval_analytics_updates_after_approval(client, leave_input):
    leave = pending(client, leave_input)
    before = api(client, "GET", "/analytics/approval", user="u3001")["summary"]
    api(client, "POST", f"/leave-requests/{leave['id']}/approve", {"reason": "同意"}, user="u2001")
    after = api(client, "GET", "/analytics/approval", user="u3001")["summary"]
    assert after["total"] == before["total"]
    assert after["approved"] == before["approved"] + 1
    export = client.get("/api/analytics/approval/export", headers=__import__("conftest").auth_headers(client.app, "u3001"))
    assert export.status_code == 200
    assert "requestId" in export.text
