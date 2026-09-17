import sqlite3

import pytest

from conftest import api, auth_headers, create, employee_update, pending
from oa.db import Database


def onboard(client, data):
    return api(client, "POST", "/employees", data, "u3001", 201)


def test_onboarding_defaults_and_full_flow(client, app, employee_input, leave_input):
    value = onboard(client, employee_input)
    employee = value["employee"]
    assert employee["managerId"] == "u2001"
    assert employee["role"] == "employee"
    assert value["profile"]["approvalReady"]
    assert len(app.state.db.balances(employee["id"])) == 3
    assert app.state.db.organization_actions(employee["id"])[0]["action"] == "onboard"
    assert api(client, "GET", f"/employees/{employee['id']}", user=employee["id"])["actions"] == []
    leave = pending(client, leave_input, employee["id"])
    assert api(client, "POST", f"/leave-requests/{leave['id']}/approve", user="u2001")["leave"]["status"] == "approved"


@pytest.mark.parametrize("user", ["u1001", "u2001"])
@pytest.mark.parametrize(
    "path,data",
    [
        ("/positions", {"name": "董事长"}),
        (
            "/roles",
            {"name": "admin", "permissions": {"manageOrganization": True, "leadTeam": True, "approveLeave": True}},
        ),
        ("/departments", {"name": "新部门", "parentId": None, "leaderId": None}),
    ],
)
def test_admin_only_writes(client, user, path, data):
    api(client, "POST", path, data, user, 403)


def test_custom_positions_and_roles(client, app, employee_input):
    position = api(client, "POST", "/positions", {"name": "  董事长  "}, "u3001", 201)["position"]
    assert position["name"] == "董事长"
    api(client, "POST", "/positions", {"name": "董事长"}, "u3001", 400)
    permissions = {"manageOrganization": False, "manageAccounts": False, "leadTeam": False, "approveLeave": True}
    role = api(client, "POST", "/roles", {"name": "独立审批员", "permissions": permissions}, "u3001", 201)["role"]
    employee = onboard(client, {**employee_input, "title": "董事长", "role": role["id"]})["employee"]
    assert employee["permissions"] == permissions
    assert len([p for p in app.state.db.positions() if p["name"] == "董事长"]) == 1
    api(client, "POST", "/departments", {"name": "测试", "parentId": None, "leaderId": employee["id"]}, "u3001", 403)
    api(client, "POST", "/roles", {"name": "独立审批员", "permissions": permissions}, "u3001", 400)


def test_no_manager_and_independent_approver(client, app, employee_input, leave_input):
    value = onboard(client, {**employee_input, "managerId": None, "noManager": True})
    employee = value["employee"]
    assert not value["profile"]["approvalReady"]
    leave = create(client, leave_input, employee["id"])
    api(client, "POST", f"/leave-requests/{leave['id']}/submit", user=employee["id"], status=400)
    assert len(app.state.db.actions(leave["id"])) == 1
    changed = employee_update(employee, leaveApproverId="u3001")
    api(client, "PUT", f"/employees/{employee['id']}", changed, "u3001")
    result = api(client, "POST", f"/leave-requests/{leave['id']}/submit", user=employee["id"])
    assert result["leave"]["currentApproverId"] == "u3001"
    changed["leaveApproverId"] = employee["id"]
    api(client, "PUT", f"/employees/{employee['id']}", changed, "u3001", 400)


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "u1001"},
        {"employeeNo": "U1001"},
        {"managerId": "missing"},
        {"managerId": "u1001"},
        {"managerId": None},
        {"noManager": True, "managerId": "u2001"},
        {"hiredAt": "2099-01-01"},
        {"hiredAt": "2026-02-30"},
        {"leaveEntitlements": {"annual": -1, "personal": 0, "sick": 0}},
        {"noManager": None},
    ],
)
def test_invalid_onboarding_is_atomic(client, app, employee_input, changes):
    counts = {
        table: app.state.db.one(f"SELECT count(*) AS n FROM {table}")["n"]
        for table in ("users", "leave_balances", "positions", "organization_actions")
    }
    response = client.post("/api/employees", headers=auth_headers(app, "u3001"), json={**employee_input, **changes})
    assert response.status_code in (400, 403, 404)
    assert "message" in response.json()
    for table, count in counts.items():
        assert app.state.db.one(f"SELECT count(*) AS n FROM {table}")["n"] == count


def test_onboarding_rolls_back_on_storage_failure(client, app, employee_input):
    app.state.db.execute(
        "CREATE TRIGGER fail_balance BEFORE INSERT ON leave_balances BEGIN SELECT RAISE(ABORT, 'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        onboard(client, employee_input)
    assert len(app.state.db.users()) == 4
    assert not app.state.db.one("SELECT id FROM positions WHERE name='实施顾问'")
    assert not app.state.db.one("SELECT id FROM organization_actions")


def test_cycles_and_leader_change(client, app):
    db = app.state.db
    department = next(d for d in db.departments() if d["id"] == db.user("u1001")["departmentId"])
    api(
        client,
        "PUT",
        f"/departments/{department['id']}",
        {"name": department["name"], "parentId": department["id"], "leaderId": "u2001"},
        "u3001",
        400,
    )
    api(client, "PUT", "/employees/u2001", employee_update(db.user("u2001"), managerId="u2001"), "u3001", 400)
    api(client, "PUT", "/employees/u3001", employee_update(db.user("u3001"), managerId="u2001"), "u3001", 400)
    api(
        client,
        "PUT",
        f"/departments/{department['id']}",
        {"name": "新研发部", "parentId": department["parentId"], "leaderId": "u3001"},
        "u3001",
    )
    assert db.user("u1001")["managerId"] == "u2001"
    assert db.user("u1001")["department"] == "新研发部"


def test_deactivation_guards_and_inactive_auth(client, app):
    db = app.state.db
    api(client, "PUT", "/employees/u2001", employee_update(db.user("u2001"), status="inactive"), "u3001", 400)
    api(client, "PUT", "/employees/u3001", employee_update(db.user("u3001"), status="inactive"), "u3001", 403)
    api(client, "PUT", "/employees/u1002", employee_update(db.user("u1002"), status="inactive"), "u3001")
    api(client, "GET", "/bootstrap", user="u1002", status=401)
    api(client, "GET", "/employees/u1002", status=404)
    assert api(client, "GET", "/employees/u1002", user="u3001")["employee"]["status"] == "inactive"


def test_custom_admin_cannot_remove_own_permissions(client, employee_input, admin):
    role = api(
        client,
        "POST",
        "/roles",
        {"name": "组织运营", "permissions": {"manageOrganization": True, "leadTeam": False, "approveLeave": False}},
        admin,
        201,
    )["role"]
    employee = api(client, "POST", "/employees", {**employee_input, "role": role["id"]}, admin, 201)["employee"]
    api(client, "POST", "/positions", {"name": "客户代表"}, employee["id"], 201)
    api(client, "PUT", f"/employees/{employee['id']}", employee_update(employee, role="employee"), employee["id"], 403)


def test_legacy_database_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as sql:
        sql.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
            title TEXT NOT NULL, role TEXT NOT NULL, manager_id TEXT REFERENCES users(id));
            CREATE TABLE leave_balances(user_id TEXT,leave_type TEXT,total_hours REAL,used_hours REAL,PRIMARY KEY(user_id,leave_type));
            INSERT INTO users VALUES('old-leader','旧领导','旧部门','负责人','manager',NULL);
            INSERT INTO users VALUES('old-user','旧员工','旧部门','顾问','employee','old-leader');
            INSERT INTO leave_balances VALUES('old-user','annual',120,17.5);""")
    db = Database(path)
    user = db.user("old-user")
    assert user["name"] == "旧员工" and user["managerId"] == "old-leader"
    assert db.remaining("old-user", "annual") == 102.5
    departments, positions = db.departments(), db.positions()
    db.close()
    db = Database(path)
    assert db.departments() == departments and db.positions() == positions
    assert len(db.users()) == 2
    assert db.all("PRAGMA foreign_key_check") == []
    db.close()
