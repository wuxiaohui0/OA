from datetime import date, datetime
from uuid import uuid4

from .domain import BusinessError, SHANGHAI, now_iso, require


class OrganizationService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def require_access(actor):
        require(actor["status"] == "active" and actor["permissions"]["manageOrganization"], "没有组织与人员管理权限")

    @staticmethod
    def privileged(permissions):
        return permissions["manageOrganization"] or permissions["manageAccounts"]

    def require_account_access(self, actor, employee):
        self.require_access(actor)
        require(
            actor["permissions"]["manageAccounts"] or not self.privileged(employee["permissions"]),
            "HR 和管理员账号仅限系统管理员管理",
            403,
        )

    def require_role_assignment(self, actor, role):
        require(
            actor["permissions"]["manageAccounts"] or not self.privileged(role["permissions"]),
            "仅系统管理员可以授予 HR 或管理员权限",
            403,
        )

    def department(self, department_id):
        value = next((d for d in self.db.departments() if d["id"] == department_id), None)
        require(value, "所属组织不存在")
        return value

    def role(self, role_id):
        value = next((r for r in self.db.roles() if r["id"] == role_id), None)
        require(value, "权限角色不存在")
        return value

    def manager(self, manager_id):
        value = self.db.user(manager_id)
        require(value, "领导不存在")
        require(value["status"] == "active", "领导账号已停用，请联系组织管理员重新指定")
        require(value["permissions"]["leadTeam"], "直属领导或组织负责人须具备团队负责人权限")
        return value

    def leave_approver(self, approver_id):
        value = self.db.user(approver_id)
        require(value, "请假审批人不存在")
        require(value["status"] == "active", "请假审批人账号已停用，请重新指定")
        require(value["permissions"]["approveLeave"], "所选人员没有请假审批权限，请重新指定请假审批人")
        return value

    def require_approver(self, user):
        self.department(user["departmentId"])
        require(user["status"] == "active", "账号已停用")
        if not user["noManager"]:
            require(user["managerId"], "尚未配置直属领导，请联系组织管理员完善汇报关系")
            require(user["managerId"] != user["id"], "直属领导不能是本人")
            self.manager(user["managerId"])
        approver_id = user["leaveApproverId"] or (None if user["noManager"] else user["managerId"])
        require(approver_id, "已设为无直属领导，请联系组织管理员指定请假审批人后再提交")
        require(approver_id != user["id"], "请假审批人不能是本人")
        return self.leave_approver(approver_id)

    def profile(self, user):
        departments = {d["id"]: d for d in self.db.departments()}
        path, seen = [], set()
        department = departments.get(user["departmentId"])
        while department and department["id"] not in seen:
            seen.add(department["id"])
            path.insert(0, department)
            department = departments.get(department["parentId"])
        chain, seen = [], {user["id"]}
        manager = self.db.user(user["managerId"]) if user["managerId"] else None
        while manager and manager["id"] not in seen:
            seen.add(manager["id"])
            chain.append(manager)
            manager = self.db.user(manager["managerId"]) if manager["managerId"] else None
        approver, issue = None, None
        try:
            approver = self.require_approver(user)
        except BusinessError as error:
            issue = str(error)
        return {
            "departmentPath": path,
            "manager": chain[0] if chain else None,
            "managementChain": chain,
            "directReports": [u for u in self.db.users() if u["managerId"] == user["id"] and u["status"] == "active"],
            "leaveApprover": approver,
            "approvalIssue": issue,
            "approvalReady": approver is not None,
        }

    def validate_employee(self, user_id, data):
        self.department(data["departmentId"])
        self.role(data["role"])
        require(
            not self.db.one(
                "SELECT id FROM users WHERE employee_no=? COLLATE NOCASE AND id<>?", data["employeeNo"], user_id
            ),
            "工号已存在，请使用其他工号",
        )
        if data.get("hiredAt") is not None:
            try:
                hired = date.fromisoformat(data["hiredAt"])
                require(hired.isoformat() == data["hiredAt"], "入职日期格式不正确")
            except (ValueError, TypeError):
                raise BusinessError("入职日期格式不正确") from None
            require(hired <= datetime.now(SHANGHAI).date(), "入职日期不能晚于今天")
        require(not (data.get("noManager") and data.get("managerId")), "已选择无直属领导，不能同时指定领导")
        require(
            data.get("managerId") or data.get("noManager") or data["status"] != "active",
            "请指定直属领导，或明确选择无直属领导",
        )
        if data.get("leaveApproverId"):
            require(data["leaveApproverId"] != user_id, "请假审批人不能是本人")
            if data["status"] == "active":
                self.leave_approver(data["leaveApproverId"])
        manager_id, seen = data.get("managerId"), {user_id}
        while manager_id:
            require(manager_id not in seen, "上下级关系不能包含本人或形成循环")
            seen.add(manager_id)
            manager = self.db.user(manager_id)
            require(manager, "领导不存在")
            if manager_id == data.get("managerId") and data["status"] == "active":
                self.manager(manager_id)
            manager_id = manager["managerId"]

    def ensure_position(self, actor, name):
        existing = self.db.one("SELECT id,name FROM positions WHERE name=? COLLATE NOCASE", name)
        if existing:
            return existing
        value = {"id": str(uuid4()), "name": name}
        self.db.execute("INSERT INTO positions VALUES(?,?)", value["id"], name)
        self.db.record_organization(value["id"], actor, "create_position", None, value)
        return value

    def create_position(self, actor, name):
        self.require_access(actor)
        with self.db.transaction():
            require(
                not self.db.one("SELECT id FROM positions WHERE name=? COLLATE NOCASE", name),
                "职位已存在，请选择已有职位",
            )
            return self.ensure_position(actor, name)

    def create_role(self, actor, data):
        self.require_access(actor)
        self.require_role_assignment(actor, data)
        require(
            not data["permissions"]["manageAccounts"] or data["permissions"]["manageOrganization"],
            "账号管理权限须同时开启组织与人员管理",
            400,
        )
        with self.db.transaction():
            require(
                not self.db.one("SELECT id FROM permission_roles WHERE name=? COLLATE NOCASE", data["name"]),
                "角色已存在，请选择已有角色",
            )
            value = {"id": str(uuid4()), **data}
            permissions = data["permissions"]
            self.db.execute(
                "INSERT INTO permission_roles VALUES(?,?,?,?,?,?)",
                value["id"],
                value["name"],
                int(permissions["manageOrganization"]),
                int(permissions["leadTeam"]),
                int(permissions["approveLeave"]),
                int(permissions["manageAccounts"]),
            )
            self.db.record_organization(value["id"], actor, "create_role", None, value)
            return value

    def onboard(self, actor, data, password_hash):
        self.require_access(actor)
        self.require_role_assignment(actor, self.role(data["role"]))
        with self.db.transaction():
            department = self.department(data["departmentId"])
            user_id = str(uuid4())
            data = {**data, "noManager": data.get("noManager") or False}
            data.setdefault("managerId", None if data["noManager"] else department["leaderId"])
            data.setdefault("leaveApproverId", None)
            self.validate_employee(user_id, data)
            require(
                not self.db.one("SELECT id FROM users WHERE username=? COLLATE NOCASE", data["username"]),
                "账号已存在，请使用其他账号",
            )
            position = self.ensure_position(actor, data["title"])
            self.db.execute(
                """INSERT INTO users(id,username,employee_no,name,department,department_id,title,role,manager_id,
                no_manager,leave_approver_id,hired_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'active')""",
                user_id,
                data["username"],
                data["employeeNo"],
                data["name"],
                department["name"],
                department["id"],
                position["name"],
                data["role"],
                data["managerId"],
                int(data["noManager"]),
                data["leaveApproverId"],
                data["hiredAt"],
            )
            for kind, hours in data["leaveEntitlements"].items():
                self.db.execute("INSERT INTO leave_balances VALUES(?,?,?,0)", user_id, kind, hours)
                self.db.record_balance(f"opening:{user_id}:{kind}", user_id, kind, hours, 0,
                                       "入职初始额度", actor["id"], actor["name"])
            self.db.execute("INSERT INTO auth_credentials VALUES(?,?,1,?)", user_id, password_hash, now_iso())
            user = self.db.user(user_id)
            self.db.record_organization(
                user_id, actor, "onboard", None, {**user, "leaveEntitlements": data["leaveEntitlements"]}
            )
            return user

    def update_employee(self, actor, user_id, data):
        self.require_access(actor)
        with self.db.transaction():
            before = self.db.user(user_id)
            require(before, "员工不存在")
            data = {**data}
            for key in ("noManager", "leaveApproverId"):
                data.setdefault(key, before[key])
            role = self.role(data["role"])
            if actor["id"] != user_id:
                self.require_account_access(actor, before)
            if data["role"] != before["role"]:
                self.require_role_assignment(actor, role)
            if actor["id"] == user_id:
                require(
                    data["status"] == "active" and role["permissions"]["manageOrganization"],
                    "不能停用自己的账号或移除自己的组织管理权限",
                )
                require(
                    not before["permissions"]["manageAccounts"] or role["permissions"]["manageAccounts"],
                    "不能移除自己的系统管理员权限",
                    403,
                )
            self.validate_employee(user_id, data)
            inactive = data["status"] == "inactive"
            if inactive or (before["permissions"]["leadTeam"] and not role["permissions"]["leadTeam"]):
                require(
                    not any(u["managerId"] == user_id and u["status"] == "active" for u in self.db.users()),
                    "请先为直属下属重新指定领导",
                )
                require(
                    not any(d["leaderId"] == user_id for d in self.db.departments()), "请先更换该员工负责的组织负责人"
                )
            if inactive or (before["permissions"]["approveLeave"] and not role["permissions"]["approveLeave"]):
                require(not self.db.one("SELECT id FROM leave_cancellations WHERE approver_id=? AND status='pending'", user_id),
                        "该员工仍有待处理的销假审批，请先转交或处理完成")
                require(
                    not any(
                        u["status"] == "active"
                        and (
                            u["leaveApproverId"] == user_id
                            or (not u["leaveApproverId"] and not u["noManager"] and u["managerId"] == user_id)
                        )
                        for u in self.db.users()
                    ),
                    "请先为相关员工重新指定请假审批人",
                )
                require(
                    not self.db.one(
                        """SELECT id FROM leave_requests WHERE deleted_at IS NULL AND (
                    (current_approver_id=? AND status='human_reviewing') OR
                    (submission_manager_id=? AND status IN ('submitted','validating','agent_reviewing'))) LIMIT 1""",
                        user_id,
                        user_id,
                    ),
                    "该员工仍有待处理的审批，请处理完成后再停用或调整角色",
                )
            if inactive:
                require(not self.db.one("""SELECT c.id FROM leave_cancellations c JOIN leave_requests lr ON lr.id=c.request_id
                    WHERE lr.applicant_id=? AND c.status='pending'""", user_id), "该员工仍有进行中的销假申请，请先完成或撤回")
                require(
                    not self.db.one(
                        """SELECT id FROM leave_requests WHERE applicant_id=? AND deleted_at IS NULL
                    AND status IN ('submitted','validating','agent_reviewing','human_reviewing','need_information') LIMIT 1""",
                        user_id,
                    ),
                    "该员工仍有进行中的申请，请先完成或撤回",
                )
            department = self.department(data["departmentId"])
            position = self.ensure_position(actor, data["title"])
            self.db.execute(
                """UPDATE users SET employee_no=?,name=?,department=?,department_id=?,title=?,role=?,manager_id=?,
                no_manager=?,leave_approver_id=?,hired_at=?,status=? WHERE id=?""",
                data["employeeNo"],
                data["name"],
                department["name"],
                department["id"],
                position["name"],
                data["role"],
                data["managerId"],
                int(data["noManager"] or False),
                data.get("leaveApproverId"),
                data.get("hiredAt"),
                data["status"],
                user_id,
            )
            after = self.db.user(user_id)
            if inactive or data["role"] != before["role"]:
                self.db.execute("DELETE FROM auth_sessions WHERE user_id=?", user_id)
            self.db.record_organization(user_id, actor, "update_employee", before, after)
            return after

    def save_department(self, actor, data, department_id=None):
        self.require_access(actor)
        with self.db.transaction():
            before = self.department(department_id) if department_id else None
            entity_id = department_id or str(uuid4())
            require(
                not any(
                    d["id"] != entity_id
                    and d["parentId"] == data["parentId"]
                    and d["name"].lower() == data["name"].lower()
                    for d in self.db.departments()
                ),
                "同级组织名称不能重复",
            )
            parent_id, seen = data["parentId"], {entity_id}
            while parent_id:
                require(parent_id not in seen, "组织不能挂在自身或下级组织之下")
                seen.add(parent_id)
                parent_id = self.department(parent_id)["parentId"]
            if data["leaderId"]:
                self.manager(data["leaderId"])
            self.db.execute(
                """INSERT INTO departments VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                parent_id=excluded.parent_id,leader_id=excluded.leader_id""",
                entity_id,
                data["name"],
                data["parentId"],
                data["leaderId"],
            )
            self.db.execute("UPDATE users SET department=? WHERE department_id=?", data["name"], entity_id)
            after = self.department(entity_id)
            self.db.record_organization(
                entity_id, actor, "update_department" if before else "create_department", before, after
            )
            return after
