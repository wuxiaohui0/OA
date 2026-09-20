CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
    title TEXT NOT NULL, role TEXT NOT NULL, manager_id TEXT REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS leave_balances (
    user_id TEXT NOT NULL REFERENCES users(id), leave_type TEXT NOT NULL,
    total_hours REAL NOT NULL, used_hours REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, leave_type)
);
CREATE TABLE IF NOT EXISTS leave_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    leave_type TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
    duration_hours REAL NOT NULL, reason TEXT NOT NULL, handover_user TEXT,
    handover_notes TEXT, status TEXT NOT NULL, current_approver_id TEXT REFERENCES users(id),
    agent_decision TEXT, agent_reason TEXT, agent_confidence REAL,
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS approval_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES leave_requests(id),
    actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
    action TEXT NOT NULL, reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES leave_requests(id),
    decision TEXT NOT NULL, summary TEXT NOT NULL, reason TEXT NOT NULL, missing_fields TEXT NOT NULL,
    risk_flags TEXT NOT NULL, policy_references TEXT NOT NULL, confidence REAL NOT NULL,
    mode TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approval_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES leave_requests(id),
    step_order INTEGER NOT NULL, approver_id TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending', comment TEXT, reviewed_at TEXT, created_at TEXT NOT NULL,
    UNIQUE(request_id, step_order)
);
CREATE INDEX IF NOT EXISTS idx_leave_applicant ON leave_requests(applicant_id);
CREATE INDEX IF NOT EXISTS idx_leave_approver ON leave_requests(current_approver_id, status);
CREATE INDEX IF NOT EXISTS idx_action_request ON approval_actions(request_id);
CREATE INDEX IF NOT EXISTS idx_action_reviewer ON approval_actions(actor_id, actor_type, action, request_id);
CREATE INDEX IF NOT EXISTS idx_approval_step_request ON approval_steps(request_id, step_order);
CREATE TABLE IF NOT EXISTS departments (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT REFERENCES departments(id), leader_id TEXT REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE);
CREATE TABLE IF NOT EXISTS permission_roles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    can_manage_organization INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_organization IN (0, 1)),
    can_lead_team INTEGER NOT NULL DEFAULT 0 CHECK(can_lead_team IN (0, 1)),
    can_approve_leave INTEGER NOT NULL DEFAULT 0 CHECK(can_approve_leave IN (0, 1)),
    can_manage_accounts INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_accounts IN (0, 1))
);
INSERT OR IGNORE INTO permission_roles (id,name,can_manage_organization,can_lead_team,can_approve_leave) VALUES ('employee', '员工', 0, 0, 0);
INSERT OR IGNORE INTO permission_roles (id,name,can_manage_organization,can_lead_team,can_approve_leave) VALUES ('manager', '主管', 0, 1, 1);
INSERT OR IGNORE INTO permission_roles (id,name,can_manage_organization,can_lead_team,can_approve_leave) VALUES ('hr', 'HR', 1, 1, 1);
CREATE TABLE IF NOT EXISTS organization_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, actor_name TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL, before_json TEXT,
    after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_credentials (
    user_id TEXT PRIMARY KEY REFERENCES users(id), password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_session_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS auth_login_attempts (
    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_started INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work_calendar (
    day TEXT PRIMARY KEY, is_workday INTEGER NOT NULL CHECK(is_workday IN (0,1)),
    name TEXT NOT NULL, updated_by TEXT NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS balance_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id), leave_type TEXT NOT NULL,
    request_id TEXT REFERENCES leave_requests(id), total_delta REAL NOT NULL, used_delta REAL NOT NULL,
    reason TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON balance_ledger(user_id, id);
CREATE TABLE IF NOT EXISTS leave_cancellations (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES leave_requests(id),
    start_at TEXT NOT NULL, end_at TEXT NOT NULL, hours REAL NOT NULL, reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','withdrawn')),
    approver_id TEXT NOT NULL REFERENCES users(id), comment TEXT, created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cancellation_request ON leave_cancellations(request_id,status);
CREATE TABLE IF NOT EXISTS cancellation_assignees (
    cancellation_id TEXT NOT NULL REFERENCES leave_cancellations(id), user_id TEXT NOT NULL REFERENCES users(id),
    PRIMARY KEY(cancellation_id,user_id)
);
CREATE TABLE IF NOT EXISTS leave_attachments (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES leave_requests(id),
    uploader_id TEXT NOT NULL REFERENCES users(id), filename TEXT NOT NULL,
    mime_type TEXT NOT NULL, size INTEGER NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachment_request ON leave_attachments(request_id);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
    request_id TEXT REFERENCES leave_requests(id), title TEXT NOT NULL, body TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id,id);
CREATE TABLE IF NOT EXISTS expense_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    category TEXT NOT NULL, amount REAL NOT NULL CHECK(amount > 0), currency TEXT NOT NULL DEFAULT 'CNY',
    occurred_at TEXT NOT NULL, description TEXT NOT NULL, payment_method TEXT NOT NULL,
    travel_request_id TEXT REFERENCES travel_requests(id),
    status TEXT NOT NULL DEFAULT 'draft', current_approver_id TEXT REFERENCES users(id),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_expense_applicant ON expense_requests(applicant_id, status);
CREATE INDEX IF NOT EXISTS idx_expense_approver ON expense_requests(current_approver_id, status);
CREATE TABLE IF NOT EXISTS expense_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES expense_requests(id),
    actor_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expense_action_request ON expense_actions(request_id, id);
CREATE TABLE IF NOT EXISTS travel_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    destination TEXT NOT NULL, purpose TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
    traveler_ids TEXT NOT NULL DEFAULT '[]', transport_standard TEXT NOT NULL DEFAULT 'economy',
    accommodation_standard TEXT NOT NULL DEFAULT 'standard', status TEXT NOT NULL DEFAULT 'draft',
    current_approver_id TEXT REFERENCES users(id), version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_travel_applicant ON travel_requests(applicant_id, status);
CREATE INDEX IF NOT EXISTS idx_travel_approver ON travel_requests(current_approver_id, status);
CREATE TABLE IF NOT EXISTS travel_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES travel_requests(id),
    actor_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_travel_action_request ON travel_actions(request_id, id);
CREATE TABLE IF NOT EXISTS procurement_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL, purpose TEXT NOT NULL, items_json TEXT NOT NULL,
    budget REAL NOT NULL CHECK(budget > 0), estimated_amount REAL NOT NULL CHECK(estimated_amount > 0),
    currency TEXT NOT NULL DEFAULT 'CNY', supplier TEXT, need_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', current_approver_id TEXT REFERENCES users(id),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_procurement_applicant ON procurement_requests(applicant_id, status);
CREATE INDEX IF NOT EXISTS idx_procurement_approver ON procurement_requests(current_approver_id, status);
CREATE TABLE IF NOT EXISTS procurement_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES procurement_requests(id),
    actor_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_procurement_action_request ON procurement_actions(request_id, id);
CREATE TABLE IF NOT EXISTS overtime_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    start_at TEXT NOT NULL, end_at TEXT NOT NULL, hours REAL NOT NULL CHECK(hours > 0),
    reason TEXT NOT NULL, compensation_type TEXT NOT NULL DEFAULT 'comp_leave',
    status TEXT NOT NULL DEFAULT 'draft', current_approver_id TEXT REFERENCES users(id),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_overtime_applicant ON overtime_requests(applicant_id, status);
CREATE INDEX IF NOT EXISTS idx_overtime_approver ON overtime_requests(current_approver_id, status);
CREATE TABLE IF NOT EXISTS overtime_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES overtime_requests(id),
    actor_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_overtime_action_request ON overtime_actions(request_id, id);
CREATE TABLE IF NOT EXISTS comp_time_requests (
    id TEXT PRIMARY KEY, applicant_id TEXT NOT NULL REFERENCES users(id),
    date TEXT NOT NULL, hours REAL NOT NULL CHECK(hours > 0), reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', current_approver_id TEXT REFERENCES users(id),
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comp_time_applicant ON comp_time_requests(applicant_id, status);
CREATE INDEX IF NOT EXISTS idx_comp_time_approver ON comp_time_requests(current_approver_id, status);
CREATE TABLE IF NOT EXISTS comp_time_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES comp_time_requests(id),
    actor_id TEXT NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comp_time_action_request ON comp_time_actions(request_id, id);
CREATE TABLE IF NOT EXISTS comp_time_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL REFERENCES users(id), overtime_request_id TEXT REFERENCES overtime_requests(id),
    comp_time_request_id TEXT REFERENCES comp_time_requests(id), delta_hours REAL NOT NULL,
    reason TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comp_time_ledger_user ON comp_time_ledger(user_id, id);
CREATE TABLE IF NOT EXISTS assistant_conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assistant_actions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL,
    target_id TEXT,
    payload TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    preview TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
