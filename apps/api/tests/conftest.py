from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from oa.app import create_app
from oa.domain import SHANGHAI
from oa.auth import COOKIE_NAME, hash_password
from oa.manage import bootstrap_admin

TEST_PASSWORD = "Test-password-123"


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(TEST_PASSWORD)


@pytest.fixture(autouse=True)
def local_model(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "AGENT_MODEL",
        "OPENAI_DEFAULT_HEADERS_JSON",
        "AGENT_TIMEOUT_MS",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def app(tmp_path, password_hash):
    app = create_app(tmp_path / "test.db")
    for user in app.state.db.users():
        app.state.auth.set_password(user["id"], password_hash, must_change=False)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin(app, password_hash):
    bootstrap_admin(app.state.db, "admin")
    user = app.state.db.one("SELECT id FROM users WHERE username='admin'")
    app.state.auth.set_password(user["id"], password_hash, must_change=False)
    return user["id"]


@pytest.fixture
def leave_input():
    date = datetime.now(SHANGHAI).date() + timedelta(days=10)
    while date.weekday() >= 5:
        date += timedelta(days=1)
    return {
        "leaveType": "sick",
        "startAt": f"{date}T09:00:00+08:00",
        "endAt": f"{date}T18:00:00+08:00",
        "reason": "身体不适需要休息",
        "handoverUser": "顾言",
        "handoverNotes": "项目已交接",
    }


@pytest.fixture
def employee_input(app):
    return {
        "username": "new.employee",
        "initialPassword": TEST_PASSWORD,
        "employeeNo": "EMP-005",
        "name": "新员工",
        "title": "实施顾问",
        "departmentId": app.state.db.user("u1001")["departmentId"],
        "hiredAt": "2026-01-01",
        "leaveEntitlements": {"annual": 80, "personal": 40, "sick": 80},
    }


def auth_headers(app, user="u1001"):
    # Domain tests start after password setup; auth tests exercise the actual login flow.
    if not app.state.db.user(user):
        return {"Cookie": f"{COOKIE_NAME}=invalid"}
    if not app.state.auth.credential(user):
        app.state.auth.set_password(user, hash_password(TEST_PASSWORD), must_change=False)
    app.state.db.execute("UPDATE auth_credentials SET must_change_password=0 WHERE user_id=?", user)
    token, session = app.state.auth.issue_session(user)
    return {"Cookie": f"{COOKIE_NAME}={token}", "x-csrf-token": session["csrfToken"]}


def api(client, method, path, data=None, user="u1001", status=200):
    response = client.request(
        method, "/api" + path, headers=auth_headers(client.app, user), **({"json": data} if data is not None else {})
    )
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def create(client, data, user="u1001"):
    return api(client, "POST", "/leave-requests", data, user, 201)["leave"]


def pending(client, data, user="u1001"):
    leave = create(client, data, user)
    return api(client, "POST", f"/leave-requests/{leave['id']}/submit", user=user)["leave"]


def employee_update(employee, **changes):
    fields = (
        "name",
        "employeeNo",
        "departmentId",
        "title",
        "role",
        "managerId",
        "noManager",
        "leaveApproverId",
        "hiredAt",
        "status",
    )
    return {**{key: employee[key] for key in fields}, **changes}
