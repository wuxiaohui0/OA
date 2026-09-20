from datetime import date, timedelta

from conftest import api


def procurement_payload():
    return {
        "title": "研发显示器",
        "purpose": "研发团队项目开发使用",
        "items": [{"name": "显示器", "quantity": 2, "unitPrice": 1200}],
        "budget": 3000,
        "currency": "CNY",
        "supplier": "办公用品供应商",
        "needBy": (date.today() + timedelta(days=14)).isoformat(),
    }


def test_procurement_full_workflow(client):
    created = api(client, "POST", "/procurement-requests", procurement_payload(), status=201)["procurement"]
    assert created["estimatedAmount"] == 2400
    assert created["items"][0]["unitPrice"] == 1200
    submitted = api(client, "POST", f"/procurement-requests/{created['id']}/submit")["procurement"]
    assert submitted["status"] == "human_reviewing"
    assert submitted["currentApproverId"] == "u2001"
    api(client, "POST", f"/procurement-requests/{created['id']}/approve", user="u1002", status=403)
    approved = api(client, "POST", f"/procurement-requests/{created['id']}/approve", user="u2001")["procurement"]
    assert approved["status"] == "approved"
    history = api(client, "GET", "/procurement-requests", user="u2001")
    assert any(item["id"] == created["id"] for item in history["history"])


def test_procurement_budget_and_rejection_reason(client):
    payload = procurement_payload()
    payload["budget"] = 100
    api(client, "POST", "/procurement-requests", payload, status=400)
    created = api(client, "POST", "/procurement-requests", procurement_payload(), status=201)["procurement"]
    api(client, "POST", f"/procurement-requests/{created['id']}/submit")
    api(client, "POST", f"/procurement-requests/{created['id']}/reject", user="u2001", data={"reason": ""}, status=400)
