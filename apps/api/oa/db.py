import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .domain import assert_transition, now_iso, require, utc_iso
from .calendar import segments, hours, subtract, clip


def camel_row(row):
    if row is None:
        return None
    return {
        key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:]): value
        for key, value in dict(row).items()
    }


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.sql = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10)
        self.sql.row_factory = sqlite3.Row
        self.sql.execute("PRAGMA foreign_keys = ON")
        self.sql.execute("PRAGMA journal_mode = WAL")
        try:
            self.migrate()
        except Exception:
            self.close()
            raise

    def close(self):
        self.sql.close()

    def execute(self, sql, *params):
        return self.sql.execute(sql, params)

    def one(self, sql, *params):
        row = self.execute(sql, *params).fetchone()
        return dict(row) if row else None

    def all(self, sql, *params):
        return [dict(row) for row in self.execute(sql, *params).fetchall()]

    @contextmanager
    def transaction(self):
        # Savepoints permit service operations to compose atomically without committing a caller's transaction.
        key = "tx_" + uuid4().hex
        self.execute(f"SAVEPOINT {key}")
        try:
            yield
            self.execute(f"RELEASE SAVEPOINT {key}")
        except BaseException:
            self.execute(f"ROLLBACK TO SAVEPOINT {key}")
            self.execute(f"RELEASE SAVEPOINT {key}")
            raise

    def migrate(self):
        with self.transaction():
            for statement in Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    self.execute(statement)
            role_columns = {row["name"] for row in self.all("PRAGMA table_info(permission_roles)")}
            if "can_manage_accounts" not in role_columns:
                self.execute(
                    "ALTER TABLE permission_roles ADD COLUMN can_manage_accounts INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_accounts IN (0,1))"
                )
            if not self.one("SELECT id FROM permission_roles WHERE id='admin'"):
                admin_name = "系统管理员"
                while self.one("SELECT id FROM permission_roles WHERE name=? COLLATE NOCASE", admin_name):
                    admin_name += "（内置）"
                self.execute("INSERT INTO permission_roles VALUES('admin',?,1,0,0,1)", admin_name)
            if not self.one("SELECT id FROM users LIMIT 1"):
                for values in [
                    ("u3001", "周宁", "人力资源部", "HR 负责人", "hr", None),
                    ("u2001", "林夏", "产品研发部", "研发总监", "manager", "u3001"),
                    ("u1001", "陈默", "产品研发部", "产品设计师", "employee", "u2001"),
                    ("u1002", "顾言", "产品研发部", "前端工程师", "employee", "u2001"),
                ]:
                    self.execute(
                        "INSERT INTO users(id, name, department, title, role, manager_id) VALUES(?,?,?,?,?,?)", *values
                    )
                    for kind, hours in [("annual", 80), ("personal", 40), ("sick", 80)]:
                        self.execute(
                            "INSERT INTO leave_balances VALUES(?,?,?,?)",
                            values[0],
                            kind,
                            hours,
                            (16 if values[0] == "u1001" else 8) if kind == "annual" else 0,
                        )
            columns = {row["name"] for row in self.all("PRAGMA table_info(users)")}
            for name, definition in {
                "username": "TEXT COLLATE NOCASE",
                "employee_no": "TEXT COLLATE NOCASE",
                "department_id": "TEXT REFERENCES departments(id)",
                "hired_at": "TEXT",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "no_manager": "INTEGER NOT NULL DEFAULT 0 CHECK(no_manager IN (0,1))",
                "leave_approver_id": "TEXT REFERENCES users(id)",
            }.items():
                if name not in columns:
                    self.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
            self.execute("UPDATE users SET username = id WHERE username IS NULL")
            self.execute("UPDATE users SET employee_no = id WHERE employee_no IS NULL")
            for col in ["username", "employee_no"]:
                self.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_user_{col} ON users({col} COLLATE NOCASE)")
            for label, col in [
                ("department", "department_id"),
                ("manager", "manager_id"),
                ("leave_approver", "leave_approver_id"),
            ]:
                self.execute(f"CREATE INDEX IF NOT EXISTS idx_user_{label} ON users({col})")
            for title in self.all("SELECT DISTINCT trim(title) AS name FROM users WHERE trim(title) <> ''"):
                self.execute("INSERT OR IGNORE INTO positions VALUES(?,?)", str(uuid4()), title["name"])
            missing = self.all("SELECT DISTINCT department FROM users WHERE department_id IS NULL")
            if missing:
                self.execute("INSERT OR IGNORE INTO departments(id,name) VALUES('org-company','FlowMind')")
            for row in missing:
                existing = self.one(
                    "SELECT id FROM departments WHERE name=? AND parent_id='org-company'", row["department"]
                )
                department_id = existing["id"] if existing else str(uuid4())
                leader = self.one(
                    """SELECT u.id FROM users u JOIN permission_roles r ON r.id=u.role
                    WHERE u.department=? AND r.can_lead_team=1 AND u.status='active' ORDER BY u.id LIMIT 1""",
                    row["department"],
                )
                self.execute(
                    "INSERT OR IGNORE INTO departments VALUES(?,?,'org-company',?)",
                    department_id,
                    row["department"],
                    leader["id"] if leader else None,
                )
                self.execute(
                    "UPDATE users SET department_id=? WHERE department=? AND department_id IS NULL",
                    department_id,
                    row["department"],
                )
            columns = {row["name"] for row in self.all("PRAGMA table_info(leave_requests)")}
            for name, definition in {
                "deleted_at": "TEXT",
                "department_snapshot": "TEXT",
                "submission_manager_id": "TEXT REFERENCES users(id)",
                "work_segments": "TEXT",
            }.items():
                if name not in columns:
                    self.execute(f"ALTER TABLE leave_requests ADD COLUMN {name} {definition}")
                    if name == "department_snapshot":
                        self.execute(
                            "UPDATE leave_requests SET department_snapshot=(SELECT department FROM users WHERE id=applicant_id) WHERE status<>'draft'"
                        )
                    if name == "submission_manager_id":
                        self.execute(
                            "UPDATE leave_requests SET submission_manager_id=current_approver_id WHERE current_approver_id IS NOT NULL"
                        )
            self.execute("""INSERT OR IGNORE INTO approval_steps(request_id,step_order,approver_id,status,created_at)
                SELECT id,1,current_approver_id,'pending',updated_at FROM leave_requests
                WHERE status='human_reviewing' AND current_approver_id IS NOT NULL""")
            self.execute("""INSERT OR IGNORE INTO approval_steps(request_id,step_order,approver_id,status,comment,reviewed_at,created_at)
                SELECT request_id,1,actor_id,CASE action WHEN 'approve' THEN 'approved' ELSE 'rejected' END,reason,created_at,created_at
                FROM approval_actions WHERE actor_type='human' AND action IN ('approve','reject') ORDER BY id""")
            for row in self.all("SELECT id,start_at,end_at FROM leave_requests WHERE work_segments IS NULL"):
                self.execute("UPDATE leave_requests SET work_segments=? WHERE id=?",
                             json.dumps(segments(self, row["start_at"], row["end_at"])), row["id"])
            for row in self.all("SELECT * FROM leave_balances"):
                self.record_balance(f"opening:{row['user_id']}:{row['leave_type']}", row["user_id"], row["leave_type"],
                                    row["total_hours"], row["used_hours"], "台账启用时的期初余额", "system", "系统")

    def roles(self):
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "permissions": {
                    "manageOrganization": bool(r["can_manage_organization"]),
                    "manageAccounts": bool(r["can_manage_accounts"]),
                    "leadTeam": bool(r["can_lead_team"]),
                    "approveLeave": bool(r["can_approve_leave"]),
                },
            }
            for r in self.all("SELECT * FROM permission_roles ORDER BY rowid")
        ]

    def departments(self):
        return [camel_row(r) for r in self.all("SELECT * FROM departments ORDER BY name,id")]

    def positions(self):
        return self.all("SELECT id,name FROM positions ORDER BY name,id")

    def user(self, user_id):
        row = self.one("SELECT * FROM users WHERE id=?", user_id)
        if not row:
            return None
        role = next((r for r in self.roles() if r["id"] == row["role"]), None)
        result = camel_row(row)
        result["noManager"] = bool(row["no_manager"])
        result["accountReady"] = bool(self.one("SELECT user_id FROM auth_credentials WHERE user_id=?", user_id))
        result["roleName"] = role["name"] if role else row["role"]
        result["permissions"] = (
            role["permissions"]
            if role
            else {"manageOrganization": False, "manageAccounts": False, "leadTeam": False, "approveLeave": False}
        )
        return result

    def users(self):
        return [self.user(row["id"]) for row in self.all("SELECT id FROM users ORDER BY role,name")]

    def balances(self, user_id):
        return [
            {
                "leaveType": r["leave_type"],
                "totalHours": r["total_hours"],
                "usedHours": r["used_hours"],
                "remainingHours": r["total_hours"] - r["used_hours"],
            }
            for r in self.all("SELECT * FROM leave_balances WHERE user_id=?", user_id)
        ]

    def remaining(self, user_id, leave_type):
        return next((r["remainingHours"] for r in self.balances(user_id) if r["leaveType"] == leave_type), 0)

    LEAVE_SELECT = """SELECT lr.*, u.name AS applicant_name, COALESCE(lr.department_snapshot,u.department) AS department,
        a.name AS current_approver_name FROM leave_requests lr JOIN users u ON u.id=lr.applicant_id
        LEFT JOIN users a ON a.id=lr.current_approver_id"""

    def map_leave(self, row):
        return {
            k: json.loads(v or "[]") if k == "workSegments" else v
            for k, v in camel_row(row).items()
            if k not in ("deletedAt", "departmentSnapshot", "submissionManagerId")
        }

    def leave(self, request_id, include_deleted=True):
        row = self.one(
            self.LEAVE_SELECT + " WHERE lr.id=? AND (? OR lr.deleted_at IS NULL)", request_id, include_deleted
        )
        return self.map_leave(row) if row else None

    def list_leaves(self, user_id):
        return {
            "mine": [
                self.map_leave(r)
                for r in self.all(
                    self.LEAVE_SELECT
                    + " WHERE lr.applicant_id=? AND lr.deleted_at IS NULL ORDER BY lr.created_at DESC",
                    user_id,
                )
            ],
            "inbox": self.pending(user_id),
            "approvalHistory": self.history(user_id),
        }

    def pending(self, user_id):
        return [
            self.map_leave(r)
            for r in self.all(
                self.LEAVE_SELECT
                + " WHERE lr.current_approver_id=? AND lr.status='human_reviewing' AND lr.deleted_at IS NULL ORDER BY lr.created_at DESC",
                user_id,
            )
        ]

    def history(self, user_id):
        rows = self.all(
            """SELECT lr.id, a.action, a.created_at, a.reason FROM approval_actions a
            JOIN leave_requests lr ON lr.id=a.request_id WHERE a.id IN (
                SELECT MAX(id) FROM approval_actions WHERE actor_id=? AND actor_type='human'
                AND action IN ('approve','reject','request_information','transfer','cancel_approved','cancel_rejected') GROUP BY request_id)
            ORDER BY a.created_at DESC,a.id DESC""",
            user_id,
        )
        return [
            {
                **self.leave(row["id"]),
                "reviewDecision": {"approve": "approved", "reject": "rejected", "request_information": "returned", "transfer": "transferred", "cancel_approved": "cancellation_approved", "cancel_rejected": "cancellation_rejected"}[row["action"]],
                "reviewedAt": row["created_at"],
                "reviewComment": row["reason"],
            }
            for row in rows
        ]

    def overlap(self, leave):
        candidates = self.all(
                """SELECT id FROM leave_requests WHERE applicant_id=? AND id<>? AND deleted_at IS NULL
            AND status IN ('submitted','validating','agent_reviewing','human_reviewing','approved') AND start_at<? AND end_at>?""",
                leave["applicantId"],
                leave["id"],
                leave["endAt"],
                leave["startAt"],
        )
        for row in candidates:
            other = self.leave(row["id"])
            active = other["workSegments"]
            for cancelled in self.all("SELECT start_at,end_at FROM leave_cancellations WHERE request_id=? AND status='approved'", row["id"]):
                active = subtract(active, cancelled["start_at"], cancelled["end_at"])
            if any(clip(active, a, b) for a, b in leave["workSegments"]):
                return True
        return False

    def create_leave(self, user, data):
        request_id, now = str(uuid4()), now_iso()
        with self.transaction():
            work = segments(self, data["startAt"], data["endAt"])
            self.execute(
                """INSERT INTO leave_requests(id,applicant_id,leave_type,start_at,end_at,duration_hours,reason,
                handover_user,handover_notes,status,created_at,updated_at,work_segments) VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?,?)""",
                request_id,
                user["id"],
                data["leaveType"],
                utc_iso(data["startAt"]),
                utc_iso(data["endAt"]),
                hours(work),
                data["reason"],
                data.get("handoverUser"),
                data.get("handoverNotes"),
                now,
                now,
                json.dumps(work),
            )
            self.record_action(request_id, "human", user["id"], user["name"], "create", "创建请假草稿", None, "draft")
        return self.leave(request_id)

    def transition(self, request_id, status, actor_type, actor_id, actor_name, action, reason, approver_id=None):
        with self.transaction():
            current = self.leave(request_id)
            require(current, "请假申请不存在")
            assert_transition(current["status"], status)
            updated = self.execute(
                """UPDATE leave_requests SET status=?,current_approver_id=?,version=version+1,updated_at=?
                WHERE id=? AND version=? AND deleted_at IS NULL""",
                status,
                approver_id,
                now_iso(),
                request_id,
                current["version"],
            )
            require(updated.rowcount == 1, "申请已被其他操作更新，请刷新后重试", 409)
            self.record_action(request_id, actor_type, actor_id, actor_name, action, reason, current["status"], status)
        return self.leave(request_id)

    def record_action(self, request_id, actor_type, actor_id, actor_name, action, reason, before, after):
        self.execute(
            """INSERT INTO approval_actions(request_id,actor_type,actor_id,actor_name,action,reason,from_status,to_status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            request_id,
            actor_type,
            actor_id,
            actor_name,
            action,
            reason,
            before,
            after,
            now_iso(),
        )

    def actions(self, request_id):
        return [
            camel_row(row)
            for row in self.all("SELECT * FROM approval_actions WHERE request_id=? ORDER BY id", request_id)
        ]

    def steps(self, request_id):
        return [
            camel_row(r)
            for r in self.all(
                """SELECT s.*,u.name AS approver_name,u.title AS approver_title
            FROM approval_steps s JOIN users u ON u.id=s.approver_id WHERE request_id=? ORDER BY step_order""",
                request_id,
            )
        ]

    def ensure_step(self, request_id, approver):
        if self.one("SELECT id FROM approval_steps WHERE request_id=? AND status='pending'", request_id):
            return
        self.execute(
            """INSERT OR IGNORE INTO approval_steps(request_id,step_order,approver_id,status,created_at)
            VALUES(?,(SELECT COALESCE(MAX(step_order),0)+1 FROM approval_steps WHERE request_id=?),?,'pending',?)""",
            request_id,
            request_id,
            approver["id"],
            now_iso(),
        )

    def complete_step(self, request_id, approver_id, status, comment):
        result = self.execute(
            """UPDATE approval_steps SET status=?,comment=?,reviewed_at=?
            WHERE request_id=? AND approver_id=? AND status='pending'""",
            status,
            comment,
            now_iso(),
            request_id,
            approver_id,
        )
        require(result.rowcount == 1, "未找到该审批人的待审核记录")

    def deduct_balance(self, leave):
        result = self.execute(
            """UPDATE leave_balances SET used_hours=used_hours+? WHERE user_id=? AND leave_type=?
            AND total_hours-used_hours>=?""",
            leave["durationHours"],
            leave["applicantId"],
            leave["leaveType"],
            leave["durationHours"],
        )
        require(result.rowcount == 1, "余额不足，不能批准。请退回修改或由 HR 调整额度。", 409)
        self.record_balance(f"deduct:{leave['id']}", leave["applicantId"], leave["leaveType"], 0,
                            leave["durationHours"], "请假批准扣减", "system", "审批流程", leave["id"])

    def record_balance(self, key, user_id, kind, total, used, reason, actor_id, actor_name, request_id=None):
        self.execute("""INSERT OR IGNORE INTO balance_ledger
            (entry_key,user_id,leave_type,request_id,total_delta,used_delta,reason,actor_id,actor_name,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", key, user_id, kind, request_id, total, used, reason, actor_id, actor_name, now_iso())

    def notify(self, user_id, request_id, title, body, key=None):
        self.execute("""INSERT OR IGNORE INTO notifications(user_id,request_id,title,body,event_key,created_at)
                     VALUES(?,?,?,?,?,?)""", user_id, request_id, title, body, key or str(uuid4()), now_iso())

    def save_review(self, request_id, review):
        self.execute(
            """INSERT INTO agent_reviews(request_id,decision,summary,reason,missing_fields,risk_flags,policy_references,confidence,mode,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            request_id,
            review["decision"],
            review["summary"],
            review["reason"],
            json.dumps(review["missingFields"]),
            json.dumps(review["riskFlags"]),
            json.dumps(review["policyReferences"]),
            review["confidence"],
            review["mode"],
            now_iso(),
        )
        self.execute(
            "UPDATE leave_requests SET agent_decision=?,agent_reason=?,agent_confidence=?,updated_at=? WHERE id=?",
            review["decision"],
            review["reason"],
            review["confidence"],
            now_iso(),
            request_id,
        )

    def record_organization(self, entity_id, actor, action, before, after):
        self.execute(
            """INSERT INTO organization_actions(entity_id,actor_id,actor_name,action,before_json,after_json,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            entity_id,
            actor["id"],
            actor["name"],
            action,
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            now_iso(),
        )

    def organization_actions(self, entity_id):
        return [
            {
                "id": r["id"],
                "entityId": r["entity_id"],
                "actorName": r["actor_name"],
                "action": r["action"],
                "before": json.loads(r["before_json"]) if r["before_json"] else None,
                "after": json.loads(r["after_json"]),
                "createdAt": r["created_at"],
            }
            for r in self.all("SELECT * FROM organization_actions WHERE entity_id=? ORDER BY id DESC", entity_id)
        ]
