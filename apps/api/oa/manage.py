"""Local bootstrap command; administrator registration is never exposed over HTTP."""

import argparse
import os
import re
import secrets
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from .app import API_ROOT, PROJECT_ROOT
from .auth import AuthService, hash_password
from .db import Database
from .domain import BusinessError, require


def bootstrap_admin(db, username):
    require(
        not db.one(
            "SELECT u.id FROM users u JOIN permission_roles r ON r.id=u.role WHERE r.can_manage_accounts=1 LIMIT 1"
        ),
        "系统管理员已初始化，请由现有管理员管理账号，不能再次初始化",
        400,
    )
    require(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._\-]{2,63}", username),
        "管理员账号须为 3 至 64 位字母、数字、点、下划线或短横线",
        400,
    )
    require(
        not db.one("SELECT id FROM users WHERE username=? COLLATE NOCASE", username),
        "账号已存在，请为系统管理员使用独立账号",
        400,
    )
    password = "Admin-" + secrets.token_urlsafe(18) + "9"
    encoded = hash_password(password)
    auth = AuthService(db)
    with db.transaction():
        require(
            not db.one(
                "SELECT u.id FROM users u JOIN permission_roles r ON r.id=u.role WHERE r.can_manage_accounts=1 LIMIT 1"
            ),
            "系统管理员已初始化",
            400,
        )
        department = db.one("SELECT id,name FROM departments WHERE parent_id IS NULL ORDER BY rowid LIMIT 1")
        require(department, "请先配置公司组织", 400)
        user_id = str(uuid4())
        db.execute(
            """INSERT INTO users(id,username,employee_no,name,department,department_id,title,role,manager_id,
            no_manager,leave_approver_id,hired_at,status) VALUES(?,?,?,'系统管理员',?,?,'系统管理员','admin',NULL,1,NULL,NULL,'active')""",
            user_id,
            username,
            "ADMIN-" + user_id[:8],
            department["name"],
            department["id"],
        )
        db.execute("INSERT OR IGNORE INTO positions VALUES(?,'系统管理员')", str(uuid4()))
        for kind in ("annual", "personal", "sick"):
            db.execute("INSERT INTO leave_balances VALUES(?,?,0,0)", user_id, kind)
        user = db.user(user_id)
        auth.set_password(user["id"], encoded)
        db.record_organization(
            user["id"], user, "initialize_admin", None, {"username": username, "role": "admin", "accountReady": True}
        )
    return password


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Initialize the first system administrator on this computer")
    parser.add_argument("command", choices=["bootstrap-admin"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    configured = Path(os.getenv("DATABASE_PATH", "./data/smart-oa.db"))
    db = Database(configured if configured.is_absolute() else API_ROOT / configured)
    try:
        password = bootstrap_admin(db, args.username)
        print(f"账号：{args.username}\n初始密码：{password}\n首次登录必须修改密码。请将初始密码私下交付给账号本人。")
    except BusinessError as error:
        parser.exit(1, str(error) + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
