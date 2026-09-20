from conftest import api


def expense_input():
    return {
        "category": "travel",
        "amount": 328.5,
        "currency": "CNY",
        "occurredAt": "2026-09-18",
        "description": "客户现场交通与住宿费用",
        "paymentMethod": "personal",
    }


def test_expense_submission_and_approval(client):
    draft = api(client, "POST", "/expenses", expense_input(), status=201)["expense"]
    assert draft["status"] == "draft"
    submitted = api(client, "POST", f"/expenses/{draft['id']}/submit", {"version": draft["version"]})["expense"]
    assert submitted["status"] == "human_reviewing"
    assert submitted["currentApproverId"] == "u2001"
    api(client, "POST", f"/expenses/{draft['id']}/approve", {"reason": "同意报销"}, status=403)
    approved = api(client, "POST", f"/expenses/{draft['id']}/approve", {"reason": "票据齐全"}, user="u2001")["expense"]
    assert approved["status"] == "approved"
    history = api(client, "GET", "/expenses", user="u2001")["history"]
    assert history[0]["id"] == draft["id"]


def test_expense_rejection_requires_reason(client):
    draft = api(client, "POST", "/expenses", expense_input(), status=201)["expense"]
    api(client, "POST", f"/expenses/{draft['id']}/submit", {"version": draft["version"]})
    api(client, "POST", f"/expenses/{draft['id']}/reject", {"reason": ""}, user="u2001", status=400)
