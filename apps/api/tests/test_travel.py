from conftest import api


def travel_input():
    return {
        "destination": "上海",
        "purpose": "客户现场交付与项目评审",
        "startAt": "2026-10-12T09:00:00+08:00",
        "endAt": "2026-10-14T18:00:00+08:00",
        "travelerIds": ["u1001", "u1002"],
        "transportStandard": "high_speed",
        "accommodationStandard": "standard",
    }


def test_travel_submission_and_approval(client):
    draft = api(client, "POST", "/travel-requests", travel_input(), status=201)["travel"]
    assert draft["status"] == "draft"
    assert draft["travelerNames"] == ["陈默", "顾言"]
    submitted = api(client, "POST", f"/travel-requests/{draft['id']}/submit", {"version": draft["version"]})["travel"]
    assert submitted["status"] == "human_reviewing"
    assert submitted["currentApproverId"] == "u2001"
    approved = api(client, "POST", f"/travel-requests/{draft['id']}/approve", {"reason": "行程合理"}, user="u2001")["travel"]
    assert approved["status"] == "approved"
    expense = api(client, "POST", "/expenses", {
        "category": "travel", "amount": 180, "currency": "CNY", "occurredAt": "2026-10-13",
        "description": "高铁票", "paymentMethod": "personal", "travelRequestId": draft["id"],
    }, status=201)["expense"]
    assert expense["travelRequestId"] == draft["id"]
    assert expense["travelDestination"] == "上海"
    assert api(client, "GET", "/travel-requests", user="u2001")["history"][0]["id"] == draft["id"]


def test_travel_rejection_requires_reason_and_dates_are_valid(client):
    invalid = travel_input()
    invalid["endAt"] = invalid["startAt"]
    api(client, "POST", "/travel-requests", invalid, status=400)
    draft = api(client, "POST", "/travel-requests", travel_input(), status=201)["travel"]
    api(client, "POST", f"/travel-requests/{draft['id']}/submit", {"version": draft["version"]})
    api(client, "POST", f"/travel-requests/{draft['id']}/reject", {"reason": ""}, user="u2001", status=400)
