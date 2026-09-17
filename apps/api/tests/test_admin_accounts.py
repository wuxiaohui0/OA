import sqlite3

import pytest

from conftest import TEST_PASSWORD, api, auth_headers, employee_update
from oa.auth import digest
from oa.db import Database


def test_admin_allocates_hr_then_hr_allocates_staff(client, app, admin, employee_input):
    hr = api(client, "POST", "/employees", {**employee_input, "role": "hr"}, admin, 201)["employee"]
    assert hr["permissions"]["manageOrganization"] and not hr["permissions"]["manageAccounts"]
    response = client.post("/api/auth/login", json={"username": hr["username"], "password": TEST_PASSWORD})
    assert response.status_code == 200 and response.json()["mustChangePassword"]
    token = client.cookies.get("oa_session")
    client.headers["x-csrf-token"] = response.json()["csrfToken"]
    assert client.post("/api/employees", json=employee_input).status_code == 403
    changed = client.post(
        "/api/auth/password", json={"currentPassword": TEST_PASSWORD, "newPassword": "Hr-new-password-123"}
    )
    assert changed.status_code == 200
    client.headers["x-csrf-token"] = changed.json()["csrfToken"]
    staff = client.post("/api/employees", json={**employee_input, "username": "new.staff", "employeeNo": "STAFF-001"})
    assert staff.status_code == 201 and staff.json()["employee"]["role"] == "employee"
    assert not app.state.db.one("SELECT * FROM auth_sessions WHERE token_hash=?", digest(token))


@pytest.mark.parametrize("role", ["hr", "admin"])
def test_hr_cannot_assign_privileged_roles(client, app, admin, employee_input, role):
    before = len(app.state.db.users())
    api(client, "POST", "/employees", {**employee_input, "role": role}, "u3001", 403)
    api(client, "PUT", "/employees/u1002", employee_update(app.state.db.user("u1002"), role=role), "u3001", 403)
    api(client, "PUT", "/employees/u3001", employee_update(app.state.db.user("u3001"), role="admin"), "u3001", 403)
    assert len(app.state.db.users()) == before
    assert app.state.db.user("u1002")["role"] == "employee"
    assert app.state.db.user("u3001")["role"] == "hr"


def test_custom_roles_cannot_bypass_account_boundaries(client, app, admin, employee_input):
    permissions = {"manageOrganization": True, "leadTeam": False, "approveLeave": False}
    for manage_accounts in (True, False):
        api(
            client,
            "POST",
            "/roles",
            {"name": "试图提权", "permissions": {**permissions, "manageAccounts": manage_accounts}},
            "u3001",
            403,
        )
    role = api(client, "POST", "/roles", {"name": "人事运营", "permissions": permissions}, admin, 201)["role"]
    api(client, "POST", "/employees", {**employee_input, "role": role["id"]}, "u3001", 403)
    hr = api(client, "POST", "/employees", {**employee_input, "role": role["id"]}, admin, 201)["employee"]
    api(client, "PUT", f"/employees/{hr['id']}", employee_update(hr, name="被篡改"), "u3001", 403)
    api(client, "POST", f"/employees/{hr['id']}/password", {"initialPassword": "Reset-password-123"}, "u3001", 403)
    api(client, "POST", f"/employees/{admin}/password", {"initialPassword": "Reset-password-123"}, hr["id"], 403)
    assert app.state.db.user(hr["id"])["name"] == hr["name"]


def test_admin_can_reset_hr_but_hr_cannot_modify_admin(client, app, admin):
    old_token, _ = app.state.auth.issue_session("u3001")
    before = app.state.auth.credential(admin)
    api(client, "POST", f"/employees/{admin}/password", {"initialPassword": "Reset-password-123"}, "u3001", 403)
    api(client, "PUT", f"/employees/{admin}", employee_update(app.state.db.user(admin), name="被篡改"), "u3001", 403)
    assert app.state.auth.credential(admin) == before
    api(client, "POST", "/employees/u3001/password", {"initialPassword": "Reset-password-123"}, admin, 204)
    assert not app.state.db.one("SELECT * FROM auth_sessions WHERE token_hash=?", digest(old_token))
    assert app.state.auth.credential("u3001")["must_change_password"]


def test_role_change_revokes_sessions_and_admin_cannot_lock_itself_out(client, app, admin):
    staff = app.state.db.user("u1002")
    old_headers = auth_headers(app, staff["id"])
    api(client, "PUT", "/employees/u1002", employee_update(staff, role="hr"), admin)
    assert client.get("/api/bootstrap", headers=old_headers).status_code == 401
    administrator = app.state.db.user(admin)
    for changes in ({"role": "hr"}, {"role": "employee"}, {"status": "inactive"}):
        api(client, "PUT", f"/employees/{admin}", employee_update(administrator, **changes), admin, 403)
    assert app.state.db.user(admin) == administrator


def test_admin_permission_requires_organization_access(client, admin):
    api(
        client,
        "POST",
        "/roles",
        {
            "name": "无效管理员",
            "permissions": {
                "manageAccounts": True,
                "manageOrganization": False,
                "leadTeam": False,
                "approveLeave": False,
            },
        },
        admin,
        400,
    )


def test_previous_hr_roles_are_migrated_without_granting_admin(tmp_path):
    path = tmp_path / "legacy-roles.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""CREATE TABLE permission_roles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, can_manage_organization INTEGER NOT NULL,
            can_lead_team INTEGER NOT NULL, can_approve_leave INTEGER NOT NULL);
            INSERT INTO permission_roles VALUES('custom-hr','系统管理员',1,0,0);""")
    db = Database(path)
    roles = {role["id"]: role for role in db.roles()}
    assert roles["admin"]["permissions"]["manageAccounts"]
    assert not roles["hr"]["permissions"]["manageAccounts"]
    assert not roles["custom-hr"]["permissions"]["manageAccounts"]
    db.close()
    db = Database(path)
    assert db.roles() == list(roles.values())
    db.close()
