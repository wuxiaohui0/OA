from datetime import datetime, timedelta

from oa.domain import SHANGHAI

from conftest import api


def times(hours=2):
    start = datetime.now(SHANGHAI).replace(minute=0, second=0, microsecond=0) + timedelta(days=3)
    end = start + timedelta(hours=hours)
    return start.isoformat(), end.isoformat()


def create_overtime(client, compensation="comp_leave"):
    start, end = times()
    return api(client, "POST", "/overtime", {"startAt": start, "endAt": end, "reason": "版本发布支持", "compensationType": compensation}, status=201)["overtime"]


def test_overtime_comp_leave_adds_balance_and_comp_time_consumes_it(client):
    request = create_overtime(client)
    assert request["hours"] == 2
    request = api(client, "POST", f"/overtime/{request['id']}/submit")["overtime"]
    api(client, "POST", f"/overtime/{request['id']}/approve", user="u2001")
    listed = api(client, "GET", "/overtime")
    assert listed["balance"] == 2
    comp = api(client, "POST", "/comp-time", {"date": "2026-10-10", "hours": 1.5, "reason": "个人事务"}, status=201)["compTime"]
    api(client, "POST", f"/comp-time/{comp['id']}/submit")
    api(client, "POST", f"/comp-time/{comp['id']}/approve", user="u2001")
    assert api(client, "GET", "/overtime")["balance"] == 0.5


def test_overtime_pay_does_not_add_balance_and_comp_time_cannot_overdraw(client):
    request = create_overtime(client, "pay")
    api(client, "POST", f"/overtime/{request['id']}/submit")
    api(client, "POST", f"/overtime/{request['id']}/approve", user="u2001")
    assert api(client, "GET", "/overtime")["balance"] == 0
    comp = api(client, "POST", "/comp-time", {"date": "2026-10-10", "hours": 1, "reason": "休息"}, status=201)["compTime"]
    api(client, "POST", f"/comp-time/{comp['id']}/submit")
    api(client, "POST", f"/comp-time/{comp['id']}/approve", user="u2001", status=400)
