import asyncio
import csv
import io
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agents import model_settings, result_payload, review_leave, run_assistant
from .auth import AuthService, COOKIE_NAME, LoginInput, PasswordInput, ResetPasswordInput, digest, hash_password
from .conversations import ConversationStore
from .db import Database
from .domain import (
    BusinessError,
    DecisionInput,
    DepartmentInput,
    EmployeeInput,
    LeaveInput,
    MessageInput,
    OnboardInput,
    PositionInput,
    RoleInput,
    require,
    LeaveEditInput,
    ReasonInput,
    VersionReasonInput,
    TransferInput,
    CancellationInput,
    CalendarDayInput,
    BalanceAdjustmentInput,
    now_iso,
)
from .leave import LeaveService
from .organization import OrganizationService
from .pilot import PilotService

API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = API_ROOT.parents[1]
logger = logging.getLogger(__name__)


class ToolAPI:
    """Tools go through the same HTTP validation and authorization as the frontend."""

    def __init__(self, client):
        self.client = client

    async def call(self, method, path, key, data=None):
        response = await self.client.request(method, path, **({"json": data} if data is not None else {}))
        payload = response.json()
        require(
            response.status_code < 400 and key in payload,
            payload.get("message", "OA API 调用失败"),
            response.status_code,
        )
        return payload[key]

    async def create_draft(self, data):
        return await self.call("POST", "/api/leave-requests", "leave", data)

    async def list_pending(self):
        return await self.call("GET", "/api/leave-requests/pending-approvals", "items")

    async def decide(self, request_id, reason, decision):
        raise BusinessError("试点期间 AI 仅提供建议，请在我的审批中核对并确认结果。", 403)


def create_app(database_path=None, *, reviewer=None, assistant=None):
    if database_path is None:
        load_dotenv(PROJECT_ROOT / ".env")
        configured = Path(os.getenv("DATABASE_PATH", "./data/smart-oa.db"))
        database_path = configured if configured.is_absolute() else API_ROOT / configured
    db = Database(database_path)
    auth = AuthService(db)
    organization = OrganizationService(db)
    leaves = LeaveService(db, reviewer or review_leave)
    pilot = PilotService(db, leaves)
    conversations = ConversationStore(db=db, ttl=30 * 24 * 60 * 60)

    @asynccontextmanager
    async def lifespan(app):
        pilot.maintenance(recover=True)

        async def maintain():
            while True:
                await asyncio.sleep(60)
                try:
                    pilot.maintenance()
                except Exception as error:
                    logger.error("Pilot maintenance failed: %s", type(error).__name__)

        task = asyncio.create_task(maintain())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            db.close()

    app = FastAPI(title="Smart OA API", version="1.0.0", lifespan=lifespan)
    app.state.db, app.state.conversations = db, conversations
    app.state.leave_service = leaves
    app.state.pilot = pilot
    app.state.auth = auth
    origins = [
        value.strip().rstrip("/")
        for value in os.getenv("WEB_ORIGIN", "http://localhost:3000,http://127.0.0.1:3000").split(",")
        if value.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "x-csrf-token", "x-filename"],
    )

    @app.middleware("http")
    async def protect_requests(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            allowed = origins + [str(request.base_url).rstrip("/")]
            if origin not in allowed:
                return JSONResponse({"message": "请求来源不受信任"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BusinessError)
    async def business_error(_request, error):
        return JSONResponse({"message": str(error)}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, error):
        field = ".".join(str(part) for part in error.errors()[0]["loc"] if part != "body")
        return JSONResponse(
            {"message": f"请求参数错误：{field or '请求内容'}，请检查必填项、格式和取值。"}, status_code=400
        )

    @app.exception_handler(Exception)
    async def server_error(_request, error):
        logger.error("API request failed: %s", type(error).__name__)
        return JSONResponse({"message": "服务器处理请求失败，请稍后重试。"}, status_code=500)

    async def current_user(request: Request):
        return auth.session(request)["user"]

    User = Annotated[dict, Depends(current_user)]

    def details(leave):
        return pilot.details(leave)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "smart-oa-api"}

    @app.post("/api/auth/login")
    async def login(data: LoginInput, request: Request, response: Response):
        token, session = await auth.login(
            data.username, data.password.get_secret_value(), request.client.host if request.client else "unknown"
        )
        previous = request.cookies.get(COOKIE_NAME)
        if previous:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", digest(previous))
        auth.set_cookie(response, token)
        return session

    @app.get("/api/auth/session")
    async def session(request: Request):
        return auth.session(request, allow_password_change=True)

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response):
        auth.session(request, allow_password_change=True)
        db.execute("DELETE FROM auth_sessions WHERE token_hash=?", digest(request.cookies[COOKIE_NAME]))
        auth.clear_cookie(response)

    @app.post("/api/auth/password")
    async def change_password(data: PasswordInput, request: Request, response: Response):
        token, session = await auth.change_password(
            request, data.current_password.get_secret_value(), data.new_password.get_secret_value()
        )
        auth.set_cookie(response, token)
        return session

    @app.post("/api/employees/{employee_id}/password", status_code=204)
    async def reset_password(employee_id: str, data: ResetPasswordInput, request: Request, user: User):
        organization.require_access(user)
        require(employee_id != user["id"], "请使用修改密码功能修改自己的密码", 400)
        employee = db.user(employee_id)
        require(employee, "员工不存在")
        organization.require_account_access(user, employee)
        require(employee["status"] == "active", "请先启用该员工账号", 400)
        encoded = await run_in_threadpool(hash_password, data.initial_password.get_secret_value())
        actor = auth.session(request)["user"]
        organization.require_access(actor)
        employee = db.user(employee_id)
        organization.require_account_access(actor, employee)
        require(employee["status"] == "active", "请先启用该员工账号", 400)
        with db.transaction():
            auth.set_password(employee_id, encoded)
            db.record_organization(employee_id, actor, "reset_password", None, {"accountReady": True})

    @app.get("/api/bootstrap")
    async def bootstrap(user: User):
        requests = db.list_leaves(user["id"])
        settings = model_settings()
        return {
            "currentUser": user,
            "users": [u for u in db.users() if u["status"] == "active" or user["permissions"]["manageOrganization"]],
            "organization": organization.profile(user),
            "balances": db.balances(user["id"]),
            "cancellationInbox": pilot.cancellation_inbox(user),
            "unreadNotifications": pilot.notifications(user)["unread"],
            "approvalMode": "assist",
            **requests,
            "stats": {
                "pending": len(requests["inbox"]) + len(pilot.cancellation_inbox(user)),
                "approved": sum(item["status"] == "approved" for item in requests["mine"]),
                "inProgress": sum(
                    item["status"] in ("submitted", "validating", "agent_reviewing", "human_reviewing", "need_information")
                    for item in requests["mine"]
                ),
                "agentHandled": sum(
                    item["agentDecision"] is not None for item in requests["mine"]
                ),
            },
            "agentMode": "deep-agent" if settings["enabled"] else "deterministic-fallback",
            "agentConfig": {
                "protocol": "openai-compatible",
                "model": settings["model"],
                "baseUrl": settings["baseUrl"],
            },
        }

    @app.get("/api/organization")
    async def list_organization(user: User):
        return {
            "departments": db.departments(),
            "positions": db.positions(),
            "roles": db.roles(),
            "employees": [
                u for u in db.users() if user["permissions"]["manageOrganization"] or u["status"] == "active"
            ],
        }

    @app.post("/api/positions", status_code=201)
    async def create_position(data: PositionInput, user: User):
        return {"position": organization.create_position(user, data.name)}

    @app.post("/api/roles", status_code=201)
    async def create_role(data: RoleInput, user: User):
        return {"role": organization.create_role(user, data.wire())}

    @app.get("/api/employees/{employee_id}")
    async def employee_details(employee_id: str, user: User):
        employee = db.user(employee_id)
        require(
            employee and (employee["status"] == "active" or user["permissions"]["manageOrganization"]), "员工不存在"
        )
        return {
            "employee": employee,
            "profile": organization.profile(employee),
            "actions": db.organization_actions(employee_id) if user["permissions"]["manageOrganization"] else [],
        }

    @app.post("/api/employees", status_code=201)
    async def onboard(data: OnboardInput, request: Request, user: User):
        organization.require_access(user)
        encoded = await run_in_threadpool(hash_password, data.initial_password.get_secret_value())
        payload = {
            "role": data.role,
            "status": data.status,
            **data.wire(exclude_unset=True, exclude={"initial_password"}),
        }
        employee = organization.onboard(auth.session(request)["user"], payload, encoded)
        return {"employee": employee, "profile": organization.profile(employee)}

    @app.put("/api/employees/{employee_id}")
    async def update_employee(employee_id: str, data: EmployeeInput, user: User):
        employee = organization.update_employee(user, employee_id, data.wire(exclude_unset=True))
        return {"employee": employee, "profile": organization.profile(employee)}

    @app.post("/api/departments", status_code=201)
    async def create_department(data: DepartmentInput, user: User):
        return {"department": organization.save_department(user, data.wire())}

    @app.put("/api/departments/{department_id}")
    async def update_department(department_id: str, data: DepartmentInput, user: User):
        return {"department": organization.save_department(user, data.wire(), department_id)}

    @app.get("/api/departments/{department_id}/actions")
    async def department_actions(department_id: str, user: User):
        organization.require_access(user)
        return {"actions": db.organization_actions(department_id)}

    @app.get("/api/leave-requests/pending-approvals")
    async def pending(user: User):
        return {"items": db.pending(user["id"])}

    @app.get("/api/leave-requests/approval-history")
    async def approval_history(user: User):
        return {"items": db.history(user["id"])}

    @app.get("/api/leave-requests/{request_id}")
    async def leave_details(request_id: str, user: User):
        leave = pilot.view(request_id, user)
        return details(leave)

    @app.post("/api/leave-requests", status_code=201)
    async def create_leave(data: LeaveInput, user: User):
        return {"leave": leaves.create(user, data.wire())}

    @app.post("/api/leave-requests/{request_id}/submit")
    async def submit_leave(request_id: str, user: User, data: DecisionInput = DecisionInput()):
        return details(await leaves.submit(request_id, user, data.version))

    @app.post("/api/leave-requests/{request_id}/approve")
    async def approve_leave(request_id: str, user: User, data: DecisionInput = DecisionInput()):
        return details(leaves.decide(request_id, user, "approve", data.reason, data.version))

    @app.post("/api/leave-requests/{request_id}/reject")
    async def reject_leave(request_id: str, user: User, data: DecisionInput = DecisionInput()):
        return details(leaves.decide(request_id, user, "reject", data.reason, data.version))

    @app.put("/api/leave-requests/{request_id}")
    async def edit_leave(request_id: str, data: LeaveEditInput, user: User):
        return details(leaves.edit(request_id, user, data.wire()))

    @app.post("/api/leave-requests/{request_id}/request-information")
    async def request_information(request_id: str, data: VersionReasonInput, user: User):
        return details(leaves.decide(request_id, user, "request_information", data.reason, data.version))

    @app.post("/api/leave-requests/{request_id}/withdraw")
    async def withdraw_leave(request_id: str, data: VersionReasonInput, user: User):
        return details(leaves.withdraw(request_id, user, data.reason, data.version))

    @app.post("/api/leave-requests/{request_id}/transfer")
    async def transfer_leave(request_id: str, data: TransferInput, user: User):
        return details(pilot.transfer(request_id, user, data.wire()))

    @app.post("/api/leave-requests/{request_id}/cancellations", status_code=201)
    async def cancel_leave(request_id: str, data: CancellationInput, user: User):
        return details(pilot.cancel(request_id, user, data.wire()))

    @app.post("/api/cancellations/{key}/transfer")
    async def transfer_cancellation(key: str, data: TransferInput, user: User):
        item = db.one("SELECT request_id FROM leave_cancellations WHERE id=?", key)
        require(item, "销假申请不存在", 404)
        return details(pilot.transfer(item["request_id"], user, data.wire(), key))

    @app.post("/api/cancellations/{key}/{decision}")
    async def decide_cancellation(key: str, decision: str, data: ReasonInput, user: User):
        require(decision in ("approve", "reject", "withdraw"), "不支持的操作")
        status = {"approve": "approved", "reject": "rejected", "withdraw": "withdrawn"}[decision]
        return details(pilot.decide_cancel(key, user, status, data.reason))

    @app.post("/api/leave-requests/{request_id}/attachments", status_code=201)
    async def upload_material(request_id: str, request: Request, user: User):
        pilot.view(request_id, user)
        content = bytearray()
        async for chunk in request.stream():
            require(len(content) + len(chunk) <= 5 * 1024 * 1024, "文件不能超过 5 MB", 413)
            content.extend(chunk)
        return details(pilot.upload(request_id, user, unquote(request.headers.get("x-filename", "")), bytes(content)))

    @app.get("/api/attachments/{key}")
    async def download_material(key: str, user: User):
        item = db.one("SELECT * FROM leave_attachments WHERE id=?", key)
        require(item, "材料不存在", 404)
        pilot.view(item["request_id"], user)
        return Response(item["content"], media_type=item["mime_type"], headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["filename"], safe=""),
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})

    @app.delete("/api/attachments/{key}")
    async def remove_material(key: str, user: User):
        return details(pilot.remove_attachment(key, user))

    @app.post("/api/leave-requests/{request_id}/remind", status_code=204)
    async def remind_approver(request_id: str, user: User):
        pilot.remind(request_id, user)

    @app.get("/api/work-calendar")
    async def work_calendar(user: User):
        return {"days": pilot.calendar()}

    @app.put("/api/work-calendar")
    async def save_calendar(data: CalendarDayInput, user: User):
        return {"days": pilot.save_day(user, data.wire())}

    @app.delete("/api/work-calendar/{day}", status_code=204)
    async def reset_calendar(day: str, user: User):
        pilot.delete_day(user, day)

    @app.get("/api/balance-ledger/{employee_id}")
    async def balance_ledger(employee_id: str, user: User):
        return pilot.ledger(user, employee_id)

    @app.post("/api/balance-adjustments")
    async def adjust_balance(data: BalanceAdjustmentInput, user: User):
        return pilot.adjust_balance(user, data.wire())

    @app.get("/api/balance-ledger/{employee_id}/export")
    async def export_ledger(employee_id: str, user: User):
        data = pilot.ledger(user, employee_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["时间", "假别", "总额度变动", "已用变动", "原因", "操作人", "关联申请"])
        for entry in data["entries"]:
            values = [entry[k] for k in ("createdAt", "leaveType", "totalDelta", "usedDelta", "reason", "actorName", "requestId")]
            writer.writerow([("'" + v) if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
        return Response(output.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leave-ledger.csv"'})

    @app.get("/api/notifications")
    async def notifications(user: User):
        return pilot.notifications(user)

    @app.post("/api/notifications/read", status_code=204)
    async def read_all_notifications(user: User):
        db.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", now_iso(), user["id"])

    @app.post("/api/notifications/{key}/read", status_code=204)
    async def read_notification(key: int, user: User):
        item = db.one("SELECT id FROM notifications WHERE id=? AND user_id=?", key, user["id"])
        require(item, "消息不存在", 404)
        db.execute("UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?", now_iso(), key)

    @app.get("/api/pilot/requests")
    async def admin_requests(user: User):
        organization.require_access(user)
        return {"items": [db.map_leave(r) for r in db.all(db.LEAVE_SELECT + " WHERE lr.deleted_at IS NULL ORDER BY lr.updated_at DESC LIMIT 500")]}

    @app.delete("/api/leave-requests/{request_id}", status_code=204)
    async def delete_leave(request_id: str, user: User):
        leaves.delete(request_id, user)
        return Response(status_code=204)

    @app.get("/api/agent/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user: User):
        conversation = conversations.get(user["id"], conversation_id)
        draft = conversations.sync_draft(conversation, lambda key: db.leave(key, include_deleted=False))
        return conversations.snapshot(conversation, draft)

    @app.post("/api/agent/leave/draft")
    async def assistant_draft(data: MessageInput, request: Request, user: User):
        conversation = conversations.begin(user["id"], data.conversation_id)
        try:
            draft = conversations.sync_draft(conversation, lambda key: db.leave(key, include_deleted=False))
            if draft:
                result = result_payload(
                    "draft_created",
                    "当前草稿尚未确认，请先确认或取消。",
                    "deep-agent" if model_settings()["enabled"] else "deterministic-fallback",
                    leave=draft,
                )
                status_code = 200
            else:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://oa-internal",
                    cookies={COOKIE_NAME: request.cookies[COOKIE_NAME]},
                    headers={"x-csrf-token": request.headers.get("x-csrf-token", "")},
                ) as client:
                    result = await (assistant or run_assistant)(
                        data.message, user, ToolAPI(client), conversation["messages"]
                    )
                turn = {"role": "assistant", "content": result["message"]}
                if result["status"] in ("draft_created", "approval_completed", "rejection_completed"):
                    turn["outcome"] = "completed"
                conversation["messages"].extend([{"role": "user", "content": data.message}, turn])
                if result.get("leave"):
                    conversation["draftId"] = result["leave"]["id"]
                status_code = 201 if result["status"] == "draft_created" else 200
            snapshot = {**conversations.snapshot(conversation, result.get("leave")), "busy": False}
            return JSONResponse({**result, "conversation": snapshot}, status_code=status_code)
        finally:
            conversations.finish(conversation)

    from .assistant import install_assistant
    install_assistant(app, db, auth, leaves, pilot, organization, conversations, current_user)
    return app
