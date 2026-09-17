import time

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, employee_update
from oa.app import create_app
from oa.auth import COOKIE_NAME, digest, verify_password
from oa.domain import BusinessError
from oa.manage import bootstrap_admin


def login(client, username="u3001", password=TEST_PASSWORD, status=200):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == status, response.text
    if status == 200:
        client.headers["x-csrf-token"] = response.json()["csrfToken"]
    return response


def test_no_anonymous_or_forged_identity(client):
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap", headers={"x-user-id": "u3001"}).status_code == 401
    assert client.post("/api/roles", headers={"x-user-id": "u3001"}, json={}).status_code == 401
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/api/health").status_code == 200
    login(client, "u1001")
    assert client.get("/api/bootstrap", headers={"x-user-id": "u3001"}).json()["currentUser"]["id"] == "u1001"


def test_cookie_csrf_logout_and_expiry(client, app):
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert response.headers["cache-control"] == "no-store"
    token = client.cookies.get(COOKIE_NAME)
    row = app.state.db.one("SELECT * FROM auth_sessions")
    assert row["token_hash"] == digest(token) and token not in str(row)
    assert client.get("/api/auth/session").json()["user"]["id"] == "u3001"
    assert client.post("/api/auth/logout", headers={"x-csrf-token": "wrong"}).status_code == 403
    assert (
        client.post("/api/positions", headers={"Origin": "https://evil.example"}, json={"name": "CEO"}).status_code
        == 403
    )
    assert client.post("/api/auth/logout").status_code == 204
    assert not client.cookies.get(COOKIE_NAME)
    assert client.get("/api/bootstrap", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code == 401
    login(client)
    app.state.db.execute("UPDATE auth_sessions SET expires_at=?", int(time.time()) - 1)
    assert client.get("/api/bootstrap").status_code == 401


def test_wrong_password_and_rate_limit(client):
    login(client, "missing", status=401)
    for _ in range(10):
        login(client, "u3001", "wrong", 401)
    login(client, status=429)
    login(client, "u1001")


def test_hr_onboards_account_and_employee_must_change_password(client, app, employee_input, leave_input):
    login(client)
    response = client.post("/api/employees", json=employee_input)
    assert response.status_code == 201, response.text
    employee = response.json()["employee"]
    assert employee["accountReady"]
    credential = app.state.auth.credential(employee["id"])
    assert credential["password_hash"] != TEST_PASSWORD and verify_password(TEST_PASSWORD, credential["password_hash"])
    assert TEST_PASSWORD not in response.text
    assert "password" not in str(app.state.db.organization_actions(employee["id"])).lower()
    with TestClient(app) as staff:
        response = login(staff, employee["username"])
        assert response.json()["mustChangePassword"]
        assert staff.get("/api/bootstrap").status_code == 403
        assert staff.post("/api/leave-requests", json=leave_input).status_code == 403
        assert (
            staff.post(
                "/api/auth/password", json={"currentPassword": "wrong", "newPassword": "New-password-123"}
            ).status_code
            == 400
        )
        old_token = staff.cookies.get(COOKIE_NAME)
        response = staff.post(
            "/api/auth/password", json={"currentPassword": TEST_PASSWORD, "newPassword": "New-password-123"}
        )
        assert response.status_code == 200 and not response.json()["mustChangePassword"]
        assert staff.cookies.get(COOKIE_NAME) != old_token
        staff.headers["x-csrf-token"] = response.json()["csrfToken"]
        assert staff.get("/api/bootstrap").json()["currentUser"]["id"] == employee["id"]
        assert staff.post("/api/employees", json=employee_input).status_code == 403
        assert staff.post("/api/leave-requests", json=leave_input).status_code == 201
        assert staff.get("/api/bootstrap", headers={"Cookie": f"{COOKIE_NAME}={old_token}"}).status_code == 401
        assert staff.post("/api/auth/logout").status_code == 204
        login(staff, employee["username"], status=401)
        login(staff, employee["username"], "New-password-123")


def test_reset_password_revokes_sessions_and_preserves_history(client, app):
    token, _ = app.state.auth.issue_session("u1001")
    login(client, "u1002")
    assert (
        client.post("/api/employees/u1001/password", json={"initialPassword": "Reset-password-123"}).status_code == 403
    )
    login(client)
    assert (
        client.post("/api/employees/u3001/password", json={"initialPassword": "Reset-password-123"}).status_code == 400
    )
    before = app.state.db.balances("u1001")
    assert (
        client.post("/api/employees/u1001/password", json={"initialPassword": "Reset-password-123"}).status_code == 204
    )
    assert not app.state.db.one("SELECT * FROM auth_sessions WHERE token_hash=?", digest(token))
    assert app.state.db.balances("u1001") == before
    assert app.state.db.organization_actions("u1001")[-1]["action"] == "reset_password"
    login(client, "u1001", status=401)
    assert login(client, "u1001", "Reset-password-123").json()["mustChangePassword"]


def test_disable_revokes_login_even_after_reenable(client, app):
    token, _ = app.state.auth.issue_session("u1002")
    login(client)
    employee = app.state.db.user("u1002")
    assert client.put("/api/employees/u1002", json=employee_update(employee, status="inactive")).status_code == 200
    assert not app.state.db.one("SELECT * FROM auth_sessions WHERE token_hash=?", digest(token))
    login(client, "u1002", status=401)
    assert client.put("/api/employees/u1002", json=employee_update(employee)).status_code == 200
    assert client.get("/api/bootstrap", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code == 401


@pytest.mark.parametrize("password", ["short1", "abcdefghijklmn", "1234567890123", "a1" * 65])
def test_weak_password_rejected_atomically(client, app, employee_input, password):
    login(client)
    assert client.post("/api/employees", json={**employee_input, "initialPassword": password}).status_code == 400
    assert len(app.state.db.users()) == 4
    assert not app.state.db.organization_actions("u3001")


def test_bootstrap_only_once_and_existing_data_preserved(tmp_path, password_hash):
    path = tmp_path / "migration.db"
    app = create_app(path)
    db = app.state.db
    app.state.auth.set_password("u3001", password_hash, must_change=False)
    existing = db.users()
    hr_credentials = app.state.auth.credential("u3001")
    hr_token, _ = app.state.auth.issue_session("u3001")
    balances = db.balances("u1001")
    with pytest.raises(BusinessError):
        bootstrap_admin(db, "u1001")
    password = bootstrap_admin(db, "admin")
    assert [db.user(u["id"]) for u in existing] == existing
    assert app.state.auth.credential("u3001") == hr_credentials
    assert db.one("SELECT token_hash FROM auth_sessions WHERE token_hash=?", digest(hr_token))
    assert len(db.users()) == 5
    assert db.balances("u1001") == balances
    with pytest.raises(BusinessError):
        bootstrap_admin(db, "another-admin")
    with TestClient(app) as client:
        response = login(client, "admin", password)
        assert response.json()["user"]["permissions"]["manageAccounts"]
        assert response.json()["mustChangePassword"]
        token = client.cookies.get(COOKIE_NAME)
    reopened = create_app(path)
    with TestClient(reopened) as client:
        assert client.get("/api/auth/session", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code == 200


def test_assistant_tools_use_authenticated_session(client):
    login(client, "u1001")
    response = client.post("/api/agent/leave/draft", json={"message": "9月17号请一天病假，头疼"})
    assert response.status_code == 201, response.text
    assert response.json()["leave"]["applicantId"] == "u1001"
