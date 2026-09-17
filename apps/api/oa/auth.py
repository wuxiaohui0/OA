import hashlib
import hmac
import os
import secrets
import time

from fastapi.concurrency import run_in_threadpool
from pydantic import Field, SecretStr

from .domain import InputModel, now_iso, require

COOKIE_NAME = "oa_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_MESSAGE = "密码须为 12 至 128 位，并包含字母和数字"


class LoginInput(InputModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordInput(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class ResetPasswordInput(InputModel):
    initial_password: SecretStr = Field(min_length=12, max_length=128)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password):
    require(
        12 <= len(password) <= 128 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password),
        PASSWORD_MESSAGE,
    )
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${salt.hex()}${key.hex()}"


def verify_password(password, encoded):
    try:
        scheme, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(key.hex(), expected)
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, db):
        self.db = db
        self.dummy_hash = hash_password(secrets.token_urlsafe(24) + "A1")
        self.secure_cookie = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

    def credential(self, user_id):
        return self.db.one("SELECT * FROM auth_credentials WHERE user_id=?", user_id)

    def set_password(self, user_id, encoded, *, must_change=True):
        self.db.execute(
            """INSERT INTO auth_credentials VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            password_hash=excluded.password_hash, must_change_password=excluded.must_change_password,
            updated_at=excluded.updated_at""",
            user_id,
            encoded,
            int(must_change),
            now_iso(),
        )
        self.db.execute("DELETE FROM auth_sessions WHERE user_id=?", user_id)

    def issue_session(self, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        self.db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", now)
        self.db.execute(
            "INSERT INTO auth_sessions VALUES(?,?,?,?)", digest(token), user_id, csrf, now + SESSION_SECONDS
        )
        return token, {
            "user": self.db.user(user_id),
            "csrfToken": csrf,
            "mustChangePassword": bool(self.credential(user_id)["must_change_password"]),
        }

    def set_cookie(self, response, token):
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=self.secure_cookie,
            samesite="lax",
            path="/",
        )

    def clear_cookie(self, response):
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=self.secure_cookie, samesite="lax")

    def session(self, request, *, allow_password_change=False):
        token = request.cookies.get(COOKIE_NAME, "")
        row = (
            self.db.one(
                "SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?", digest(token), int(time.time())
            )
            if token
            else None
        )
        require(row, "登录已失效，请重新登录", 401)
        user = self.db.user(row["user_id"])
        credential = self.credential(row["user_id"])
        require(user and user["status"] == "active" and credential, "登录已失效，请重新登录", 401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            require(
                hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), row["csrf_token"].encode()),
                "会话校验失败，请刷新页面后重试",
                403,
            )
        require(allow_password_change or not credential["must_change_password"], "请先修改初始密码", 403)
        return {
            "user": user,
            "csrfToken": row["csrf_token"],
            "mustChangePassword": bool(credential["must_change_password"]),
        }

    def throttle(self, username, ip):
        now = int(time.time())
        with self.db.transaction():
            self.db.execute("DELETE FROM auth_login_attempts WHERE window_started<=?", now - 900)
            for key, limit in [("account:" + digest(username.lower()), 10), ("ip:" + digest(ip), 60)]:
                row = self.db.one("SELECT attempts FROM auth_login_attempts WHERE key=?", key)
                require(not row or row["attempts"] < limit, "登录尝试过于频繁，请 15 分钟后重试", 429)
            for key in ["account:" + digest(username.lower()), "ip:" + digest(ip)]:
                self.db.execute(
                    """INSERT INTO auth_login_attempts VALUES(?,1,?) ON CONFLICT(key)
                                DO UPDATE SET attempts=attempts+1""",
                    key,
                    now,
                )

    async def login(self, username, password, ip):
        self.throttle(username, ip)
        row = self.db.one("SELECT id FROM users WHERE username=? COLLATE NOCASE", username)
        credential = self.credential(row["id"]) if row else None
        encoded = credential["password_hash"] if credential else self.dummy_hash
        valid = await run_in_threadpool(verify_password, password, encoded)
        user = self.db.user(row["id"]) if row else None
        current = self.credential(row["id"]) if row else None
        require(
            valid and credential and current == credential and user and user["status"] == "active",
            "账号或密码错误，或账号尚未开通、已停用",
            401,
        )
        with self.db.transaction():
            self.db.execute("DELETE FROM auth_login_attempts WHERE key=?", "account:" + digest(username.lower()))
            return self.issue_session(user["id"])

    async def change_password(self, request, current_password, new_password):
        session = self.session(request, allow_password_change=True)
        user = session["user"]
        credential = self.credential(user["id"])
        require(
            await run_in_threadpool(verify_password, current_password, credential["password_hash"]),
            "当前密码不正确",
            400,
        )
        require(current_password != new_password, "新密码不能与当前密码相同", 400)
        encoded = await run_in_threadpool(hash_password, new_password)
        # Password hashing yields; re-check the session before replacing credentials.
        self.session(request, allow_password_change=True)
        require(self.credential(user["id"]) == credential, "密码已变更，请重新登录", 401)
        with self.db.transaction():
            self.set_password(user["id"], encoded, must_change=False)
            self.db.record_organization(user["id"], user, "change_password", None, {"accountReady": True})
            return self.issue_session(user["id"])
