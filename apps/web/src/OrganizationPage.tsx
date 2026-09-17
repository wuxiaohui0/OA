import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Building2, CheckCircle2, ChevronRight, KeyRound, LoaderCircle, Pencil, Plus, Save, Search, UserPlus, Users, X } from "lucide-react";
import { api, leaveTypeLabels, type BootstrapData, type Department, type DirectoryData, type EmployeeDetail, type EmployeeInput, type LeaveType, type OrganizationAction, type PermissionRole, type Position, type RolePermissions, type User } from "./api";
import PasswordField from "./PasswordField";

const permissionLabels: Record<keyof RolePermissions, string> = {
  manageAccounts: "管理 HR 和管理员账号",
  manageOrganization: "组织与人员管理（含角色维护、全部请假记录查看）",
  leadTeam: "担任直属领导或组织负责人",
  approveLeave: "审批分配给自己的请假申请",
};
function privileged(permissions: RolePermissions) { return permissions.manageOrganization || permissions.manageAccounts; }
function canManageEmployee(actor: User, employee: User) {
  return actor.permissions.manageOrganization && (actor.permissions.manageAccounts || !privileged(employee.permissions) || actor.id === employee.id);
}
function roleDescription(role: PermissionRole | undefined): string {
  return ["提交个人申请", ...Object.entries(permissionLabels).filter(([key]) => role?.permissions[key as keyof RolePermissions]).map(([, label]) => label)].join("；");
}
const actionLabels: Record<string, string> = { onboard: "入职建档", update_employee: "员工资料变更", create_department: "创建组织", update_department: "组织变更", reset_password: "设置 / 重置账号密码", change_password: "员工修改密码", initialize_account: "初始化 HR 账号", initialize_admin: "初始化系统管理员" };
const fieldLabels: Record<string, string> = { username: "账号", employeeNo: "工号", name: "姓名", departmentId: "所属组织", title: "职位名称", role: "权限角色", managerId: "直属领导", noManager: "无直属领导", leaveApproverId: "指定请假审批人", hiredAt: "入职日期", status: "账号状态", nameDepartment: "组织名称", parentId: "上级组织", leaderId: "负责人" };

function departmentPath(id: string, departments: Department[]): string {
  const parts: string[] = [];
  const seen = new Set<string>();
  let current = departments.find((item) => item.id === id);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    parts.unshift(current.name);
    current = departments.find((item) => item.id === current!.parentId);
  }
  return parts.join(" / ");
}

function departmentRows(departments: Department[], parentId: string | null = null, depth = 0): { department: Department; depth: number }[] {
  if (depth > departments.length) return [];
  return departments.filter((item) => item.parentId === parentId).flatMap((department) => [
    { department, depth }, ...departmentRows(departments, department.id, depth + 1),
  ]);
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const dialog = document.querySelector<HTMLElement>(".organization-dialog");
    dialog?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const elements = [...(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]') ?? [])];
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keydown); previous?.focus(); };
  }, [onClose]);
  return <div className="organization-backdrop"><section className="organization-dialog" role="dialog" aria-modal="true" aria-labelledby="organization-dialog-title" tabIndex={-1}>
    <header><h2 id="organization-dialog-title">{title}</h2><button className="icon-button" title="关闭" aria-label="关闭" onClick={onClose}><X size={19} /></button></header>
    {children}
  </section></div>;
}

function History({ actions, directory }: { actions: OrganizationAction[]; directory: DirectoryData }) {
  function format(key: string, value: unknown): string {
    if (key === "noManager") return value ? "是" : "否";
    if (key === "leaveApproverId" && !value) return "未单独指定";
    if (value === null || value === undefined || value === "") return "未设置";
    if (key === "managerId" || key === "leaderId" || key === "leaveApproverId") return directory.employees.find((item) => item.id === value)?.name ?? String(value);
    if (key === "departmentId" || key === "parentId") return directory.departments.find((item) => item.id === value)?.name ?? String(value);
    if (key === "role") return directory.roles.find((item) => item.id === value)?.name ?? String(value);
    if (key === "status") return value === "active" ? "启用" : "停用";
    return String(value);
  }
  return <section className="organization-history"><h3>变更记录</h3>{actions.length === 0 && <p className="muted">暂无变更记录</p>}
    {actions.map((action) => <article key={action.id}><strong>{actionLabels[action.action] ?? action.action} · {action.actorName}</strong><time>{new Date(action.createdAt).toLocaleString("zh-CN")}</time>
      {Object.entries(fieldLabels).filter(([key]) => key in action.after && action.before?.[key] !== action.after[key]).map(([key, label]) => <p key={key}>{label}：{action.before ? `${format(key, action.before[key])} → ` : ""}{format(key, action.after[key])}</p>)}
      {Boolean(action.after.leaveEntitlements) && <p>初始额度：{Object.entries(action.after.leaveEntitlements as Record<LeaveType, number>).map(([type, hours]) => `${leaveTypeLabels[type as LeaveType]} ${hours} 小时`).join("，")}</p>}
    </article>)}
  </section>;
}

function EmployeeEditor({ actor, directory, employee, initialDepartment, initialRole = "employee", onPositionCreated, onRoleCreated, onSaved, onClose }: {
  actor: User; directory: DirectoryData; employee: User | null; initialDepartment: string; initialRole?: string;
  onPositionCreated: (position: Position) => void;
  onRoleCreated: (role: PermissionRole) => void;
  onSaved: (employee: User, password?: string) => Promise<void>; onClose: () => void;
}) {
  const selectedDepartment = employee?.departmentId ?? initialDepartment;
  const [input, setInput] = useState<EmployeeInput>(employee ? {
    name: employee.name, employeeNo: employee.employeeNo, departmentId: employee.departmentId, title: employee.title,
    role: employee.role, managerId: employee.managerId, hiredAt: employee.hiredAt, status: employee.status,
    noManager: employee.noManager, leaveApproverId: employee.leaveApproverId,
  } : {
    name: "", employeeNo: "", departmentId: selectedDepartment, title: "", role: initialRole,
    managerId: directory.departments.find((item) => item.id === selectedDepartment)?.leaderId ?? null,
    noManager: false, leaveApproverId: null,
    hiredAt: new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10), status: "active",
  });
  const [username, setUsername] = useState(employee?.username ?? "");
  const [initialPassword, setInitialPassword] = useState("");
  const [entitlements, setEntitlements] = useState({ annual: 0, personal: 40, sick: 80 });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newPosition, setNewPosition] = useState<string | null>(null);
  const [positionBusy, setPositionBusy] = useState(false);
  const [positionError, setPositionError] = useState("");
  const [newRole, setNewRole] = useState<Omit<PermissionRole, "id"> | null>(null);
  const [roleBusy, setRoleBusy] = useState(false);
  const [roleError, setRoleError] = useState("");
  const availableRoles = directory.roles.filter((role) => actor.permissions.manageAccounts || !privileged(role.permissions) || role.id === employee?.role);
  const availablePermissions = Object.entries(permissionLabels).filter(([key]) => actor.permissions.manageAccounts || (key !== "manageAccounts" && key !== "manageOrganization"));
  const positionNames = [...new Set([...directory.positions.map((item) => item.name), ...(input.title ? [input.title] : [])])].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const descendants = new Set(employee ? [employee.id] : []);
  for (let i = 0; i < directory.employees.length; i++) directory.employees.forEach((item) => { if (item.managerId && descendants.has(item.managerId)) descendants.add(item.id); });
  const managers = directory.employees.filter((item) => item.status === "active" && item.permissions.leadTeam && !descendants.has(item.id));
  const availableManagers = managers.some((item) => item.id === input.managerId) ? managers : [...managers, ...directory.employees.filter((item) => item.id === input.managerId)];
  const approvers = directory.employees.filter((item) => item.status === "active" && item.permissions.approveLeave && item.id !== employee?.id);
  const availableApprovers = approvers.some((item) => item.id === input.leaveApproverId) ? approvers : [...approvers, ...directory.employees.filter((item) => item.id === input.leaveApproverId)];
  async function saveRole() {
    if (!newRole || roleBusy) return;
    if (!newRole.name.trim()) { setRoleError("请填写角色名称"); return; }
    setRoleBusy(true); setRoleError("");
    try {
      const result = await api.createRole(actor.id, newRole);
      onRoleCreated(result.role);
      setInput((current) => ({ ...current, role: result.role.id }));
      setNewRole(null);
    } catch (cause) { setRoleError(cause instanceof Error ? cause.message : "新增角色失败"); }
    finally { setRoleBusy(false); }
  }
  async function savePosition() {
    if (positionBusy) return;
    const name = newPosition?.trim();
    if (!name) { setPositionError("请填写职位名称"); return; }
    setPositionBusy(true); setPositionError("");
    try {
      const result = await api.createPosition(actor.id, name);
      onPositionCreated(result.position);
      setInput((current) => ({ ...current, title: result.position.name }));
      setNewPosition(null);
    } catch (cause) { setPositionError(cause instanceof Error ? cause.message : "新增职位失败"); }
    finally { setPositionBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || positionBusy || roleBusy || newPosition !== null || newRole !== null) return;
    setBusy(true); setError("");
    try {
      const result = employee ? await api.updateEmployee(actor.id, employee.id, input) : await api.onboard(actor.id, {
        name: input.name, employeeNo: input.employeeNo, departmentId: input.departmentId, title: input.title,
        role: input.role, managerId: input.managerId, hiredAt: input.hiredAt!, username, leaveEntitlements: entitlements,
        noManager: input.noManager, leaveApproverId: input.leaveApproverId,
        initialPassword,
      });
      await onSaved(result.employee, employee ? undefined : initialPassword);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  }
  return <Dialog title={employee ? "编辑员工资料" : initialRole === "hr" ? "分配 HR 账号" : "新员工入职"} onClose={busy ? () => {} : onClose}><form onSubmit={save}>
    <fieldset disabled={busy || positionBusy || roleBusy} className="organization-fields"><div className="form-grid">
      <label><span>姓名</span><input required maxLength={80} value={input.name} onChange={(e) => setInput({ ...input, name: e.target.value })} /></label>
      <label><span>工号</span><input required maxLength={64} value={input.employeeNo} onChange={(e) => setInput({ ...input, employeeNo: e.target.value })} /></label>
      <label><span>账号</span><input required minLength={3} maxLength={64} pattern="[a-zA-Z0-9][a-zA-Z0-9._\-]{2,63}" disabled={Boolean(employee)} value={username} onChange={(e) => setUsername(e.target.value)} /></label>
      <label><span>入职日期</span><input required={!employee} type="date" max={new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10)} value={input.hiredAt ?? ""} onChange={(e) => setInput({ ...input, hiredAt: e.target.value || null })} /></label>
      {!employee && <div className="full"><PasswordField label="初始密码" value={initialPassword} onChange={setInitialPassword} generate /><small className="permission-description">12 至 128 位，包含字母和数字。员工首次登录须修改密码。</small></div>}
      <label className="full"><span>所属组织</span><select required value={input.departmentId} onChange={(e) => {
        const department = directory.departments.find((item) => item.id === e.target.value);
        setInput({ ...input, departmentId: e.target.value, managerId: input.noManager ? null : employee ? input.managerId : department?.leaderId && managers.some((item) => item.id === department.leaderId) ? department.leaderId : null });
      }}><option value="">请选择组织</option>{departmentRows(directory.departments).map(({ department }) => <option key={department.id} value={department.id}>{departmentPath(department.id, directory.departments)}</option>)}</select></label>
      <div className="position-field">
        <label htmlFor="employee-position"><span>职位名称</span></label>
        <div className="position-control"><select id="employee-position" required value={input.title} onChange={(e) => setInput({ ...input, title: e.target.value })}><option value="">请选择职位</option>{positionNames.map((name) => <option key={name} value={name}>{name}</option>)}</select>
          <button className="icon-button" type="button" title="新增职位" aria-label="新增职位" disabled={newPosition !== null} onClick={() => { setNewPosition(""); setPositionError(""); }}><Plus size={17} /></button>
        </div>
        {newPosition !== null && <div className="position-create">
          <label htmlFor="new-position-name"><span>新职位名称</span></label>
          <div className="position-control"><input id="new-position-name" autoFocus maxLength={100} value={newPosition} onChange={(e) => { setNewPosition(e.target.value); setPositionError(""); }} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void savePosition(); }
          }} />
            <button type="button" className="icon-button" title="保存职位" aria-label="保存职位" disabled={!newPosition.trim() || positionBusy} onClick={() => void savePosition()}>{positionBusy ? <LoaderCircle size={17} className="spin" /> : <Save size={17} />}</button>
            <button type="button" className="icon-button" title="取消新增职位" aria-label="取消新增职位" onClick={() => { setNewPosition(null); setPositionError(""); }}><X size={17} /></button>
          </div>
          {positionError && <p className="form-error" role="alert">{positionError}</p>}
        </div>}
      </div>
      <div className="position-field">
        <label htmlFor="employee-role"><span>权限角色</span></label>
        <div className="position-control"><select id="employee-role" required disabled={employee?.id === actor.id} aria-describedby="employee-role-description" value={input.role} onChange={(e) => setInput({ ...input, role: e.target.value })}>{availableRoles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select>
          <button type="button" className="icon-button" title="新增角色" aria-label="新增角色" disabled={newRole !== null || employee?.id === actor.id} onClick={() => { setNewRole({ name: "", permissions: { manageOrganization: false, manageAccounts: false, leadTeam: false, approveLeave: false } }); setRoleError(""); }}><Plus size={17} /></button>
        </div>
        <small className="permission-description" id="employee-role-description">{roleDescription(directory.roles.find((role) => role.id === input.role))}</small>
        {newRole && <div className="position-create">
          <label htmlFor="new-role-name"><span>新角色名称</span></label>
          <div className="position-control"><input id="new-role-name" autoFocus maxLength={100} value={newRole.name} onChange={(e) => { setNewRole({ ...newRole, name: e.target.value }); setRoleError(""); }} onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void saveRole(); }
          }} />
            <button type="button" className="icon-button" title="保存角色" aria-label="保存角色" disabled={!newRole.name.trim() || roleBusy} onClick={() => void saveRole()}>{roleBusy ? <LoaderCircle size={17} className="spin" /> : <Save size={17} />}</button>
            <button type="button" className="icon-button" title="取消新增角色" aria-label="取消新增角色" onClick={() => { setNewRole(null); setRoleError(""); }}><X size={17} /></button>
          </div>
          <div className="role-permissions">{availablePermissions.map(([key, label]) => <label className="organization-checkbox" key={key}><input type="checkbox" checked={newRole.permissions[key as keyof RolePermissions]} onChange={(e) => setNewRole({ ...newRole, permissions: { ...newRole.permissions, [key]: e.target.checked, ...(key === "manageAccounts" && e.target.checked ? { manageOrganization: true } : {}), ...(key === "manageOrganization" && !e.target.checked ? { manageAccounts: false } : {}) } })} /><span>{label}</span></label>)}</div>
          {roleError && <p className="form-error" role="alert">{roleError}</p>}
        </div>}
      </div>
      <div className="position-field">
        <label className="organization-checkbox"><input type="checkbox" checked={input.noManager} onChange={(e) => setInput({ ...input, noManager: e.target.checked, managerId: null })} /><span>无直属领导</span></label>
        <label htmlFor="employee-manager"><span>直属领导</span></label>
        <select id="employee-manager" disabled={input.noManager} required={!input.noManager && input.status === "active"} value={input.managerId ?? ""} onChange={(e) => setInput({ ...input, managerId: e.target.value || null })}><option value="">{input.noManager ? "无直属领导" : "未指定"}</option>{availableManagers.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.department} · {item.title}{item.status === "inactive" ? "（已停用）" : ""}</option>)}</select>
      </div>
      <div className="position-field full"><label htmlFor="employee-leave-approver"><span>请假审批人</span></label><select id="employee-leave-approver" value={input.leaveApproverId ?? ""} onChange={(e) => setInput({ ...input, leaveApproverId: e.target.value || null })}><option value="">{input.noManager ? "未指定" : "跟随直属领导"}</option>{availableApprovers.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.department} · {item.title}{item.status === "inactive" ? "（已停用）" : ""}</option>)}</select>
        {!input.leaveApproverId && input.noManager && <small className="permission-description">未指定审批人，暂不可提交请假。</small>}
        {!input.leaveApproverId && input.managerId && !directory.employees.find((item) => item.id === input.managerId)?.permissions.approveLeave && <small className="permission-description">直属领导没有请假审批权限，请另选审批人。</small>}
      </div>
    </div>
    {!employee && <section className="entitlements"><h3>初始假期额度（小时）</h3><div className="entitlement-inputs">{(Object.keys(entitlements) as LeaveType[]).map((type) => <label key={type}><span>{leaveTypeLabels[type]}</span><input type="number" required min={0} max={2000} step="0.5" value={entitlements[type]} onChange={(e) => setEntitlements({ ...entitlements, [type]: Number(e.target.value) })} /></label>)}</div></section>}
    {employee && <label className="account-toggle"><input type="checkbox" disabled={employee.id === actor.id} checked={input.status === "active"} onChange={(e) => setInput({ ...input, status: e.target.checked ? "active" : "inactive" })} />账号启用</label>}
    </fieldset>
    {error && <p className="form-error" role="alert">{error}</p>}
    <footer><button type="button" className="button ghost" disabled={busy || positionBusy || roleBusy} onClick={onClose}>取消</button><button className="button primary" disabled={busy || positionBusy || roleBusy || newPosition !== null || newRole !== null}>{busy ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}{employee ? "保存变更" : "完成入职"}</button></footer>
  </form></Dialog>;
}

function PasswordReset({ employee, onSaved, onClose }: { employee: User; onSaved: (password: string) => void; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { await api.resetPassword(employee.id, password); onSaved(password); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "密码设置失败"); }
    finally { setBusy(false); }
  }
  return <Dialog title={employee.accountReady ? "重置账号密码" : "开通登录账号"} onClose={busy ? () => {} : onClose}>
    <form className="account-password-form" onSubmit={submit}><p>{employee.name} · {employee.username}</p>
      <fieldset disabled={busy} className="organization-fields"><PasswordField label="初始密码" value={password} onChange={setPassword} generate /></fieldset>
      <p>12 至 128 位，包含字母和数字。保存后原登录失效，员工下次登录须修改密码。</p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <KeyRound size={17} />}保存初始密码</button>
    </form>
  </Dialog>;
}

function DepartmentEditor({ actor, directory, department, parentId, onSaved, onClose }: {
  actor: User; directory: DirectoryData; department: Department | null; parentId: string | null;
  onSaved: (department: Department) => Promise<void>; onClose: () => void;
}) {
  const [input, setInput] = useState({ name: department?.name ?? "", parentId: department ? department.parentId : parentId, leaderId: department?.leaderId ?? null });
  const [actions, setActions] = useState<OrganizationAction[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    if (department) api.departmentActions(actor.id, department.id).then((result) => { if (active) setActions(result.actions); }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [actor.id, department]);
  const descendants = new Set(department ? [department.id] : []);
  for (let i = 0; i < directory.departments.length; i++) directory.departments.forEach((item) => { if (item.parentId && descendants.has(item.parentId)) descendants.add(item.id); });
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { const result = await api.saveDepartment(actor.id, input, department?.id); await onSaved(result.department); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); }
    finally { setBusy(false); }
  }
  return <Dialog title={department ? "编辑组织" : "新增组织"} onClose={onClose}><form onSubmit={save}><fieldset disabled={busy} className="organization-fields"><div className="form-grid">
    <label className="full"><span>组织名称</span><input required maxLength={100} value={input.name} onChange={(e) => setInput({ ...input, name: e.target.value })} /></label>
    <label className="full"><span>上级组织</span><select value={input.parentId ?? ""} onChange={(e) => setInput({ ...input, parentId: e.target.value || null })}><option value="">顶级组织</option>{departmentRows(directory.departments).filter(({ department: item }) => !descendants.has(item.id)).map(({ department: item }) => <option key={item.id} value={item.id}>{departmentPath(item.id, directory.departments)}</option>)}</select></label>
    <label className="full"><span>组织负责人</span><select value={input.leaderId ?? ""} onChange={(e) => setInput({ ...input, leaderId: e.target.value || null })}><option value="">未指定</option>{directory.employees.filter((item) => item.status === "active" && item.permissions.leadTeam).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.title}</option>)}</select></label>
    </div></fieldset>{error && <p className="form-error" role="alert">{error}</p>}<footer><button className="button ghost" type="button" disabled={busy} onClick={onClose}>取消</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}保存组织</button></footer></form>
    {department && <History actions={actions} directory={directory} />}
  </Dialog>;
}

export function OrganizationSummary({ data }: { data: BootstrapData }) {
  return <section className="organization-summary" aria-label="我的组织关系"><div><Building2 size={18} /><span>所属组织</span><strong>{data.organization.departmentPath.map((item) => item.name).join(" / ") || "未分配"}</strong></div>
    <div><Users size={18} /><span>直属领导</span><strong>{data.currentUser.noManager ? "无直属领导" : data.organization.manager ? `${data.organization.manager.name} · ${data.organization.manager.title}` : "未指定"}</strong></div>
    <div><CheckCircle2 size={18} /><span>请假审批人</span><strong>{data.organization.leaveApprover?.name ?? "未指定"}</strong></div>
    {!data.organization.approvalReady && <p role="status">{data.organization.approvalIssue}</p>}
  </section>;
}

export default function OrganizationPage({ data, onChanged }: { data: BootstrapData; onChanged: () => Promise<void> }) {
  const [directory, setDirectory] = useState<DirectoryData | null>(null);
  const [departmentId, setDepartmentId] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("active");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [editor, setEditor] = useState<{ type: "employee"; item: User | null; initialRole?: string } | { type: "department"; item: Department | null } | null>(null);
  const [detail, setDetail] = useState<EmployeeDetail | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState("");
  const [resetEmployee, setResetEmployee] = useState<User | null>(null);
  const [receipt, setReceipt] = useState<{ employee: User; password: string } | null>(null);
  const isHr = data.currentUser.permissions.manageOrganization;
  useEffect(() => {
    let active = true;
    api.organization(data.currentUser.id).then((result) => { if (active) setDirectory(result); }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [data.currentUser.id]);
  useEffect(() => {
    let active = true;
    setDetail(null); setDetailError("");
    if (detailId) api.employee(data.currentUser.id, detailId).then((result) => { if (active) setDetail(result); }).catch((cause: Error) => { if (active) setDetailError(cause.message); });
    return () => { active = false; };
  }, [detailId, data.currentUser.id]);
  async function refresh() { setDirectory(await api.organization(data.currentUser.id)); await onChanged(); }
  if (!directory) return <div className="organization-loading">{error ? <><p role="alert">{error}</p><button className="button ghost" onClick={() => void refresh().catch((cause: Error) => setError(cause.message))}>重试</button></> : <><LoaderCircle className="spin" size={20} />正在加载组织与人员</>}</div>;
  const selectedDepartment = directory.departments.find((item) => item.id === departmentId);
  const visibleDepartments = new Set(departmentId ? [departmentId] : directory.departments.map((item) => item.id));
  for (let i = 0; i < directory.departments.length; i++) directory.departments.forEach((item) => { if (item.parentId && visibleDepartments.has(item.parentId)) visibleDepartments.add(item.id); });
  const employees = directory.employees.filter((item) => visibleDepartments.has(item.departmentId) && (!status || item.status === status)
    && `${item.name} ${item.employeeNo} ${item.username} ${item.title}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="organization-page"><div className="page-heading"><div><h1>{isHr ? "组织与人员" : "通讯录"}</h1><p>{directory.departments.length} 个组织 · {directory.employees.filter((item) => item.status === "active").length} 位在职员工</p></div>
    <div className="department-actions">{data.currentUser.permissions.manageAccounts && <button className="button primary" onClick={() => { setSuccess(""); setEditor({ type: "employee", item: null, initialRole: "hr" }); }}><KeyRound size={17} />分配 HR 账号</button>}{isHr && !selectedDepartment && <button className="button ghost" onClick={() => { setSuccess(""); setEditor({ type: "employee", item: null }); }}><UserPlus size={17} />新员工入职</button>}</div></div>
    <OrganizationSummary data={data} />
    {error && <p className="form-error" role="alert">{error}</p>}
    {success && <p className="organization-success" role="status"><CheckCircle2 size={17} />{success}</p>}
    <div className="organization-layout"><aside className="organization-tree"><header><h2>组织架构</h2>{isHr && <button className="icon-button" title="新增组织" aria-label="新增组织" onClick={() => setEditor({ type: "department", item: null })}><Plus size={17} /></button>}</header>
      <button className={!departmentId ? "selected" : ""} onClick={() => setDepartmentId("")}><Users size={16} />全部人员</button>
      {departmentRows(directory.departments).map(({ department, depth }) => <button key={department.id} title={departmentPath(department.id, directory.departments)} className={department.id === departmentId ? "selected" : ""} style={{ paddingLeft: 12 + Math.min(depth, 4) * 14 }} onClick={() => setDepartmentId(department.id)}><Building2 size={16} /><span>{department.name}</span></button>)}
    </aside><section className="employee-directory">
      {selectedDepartment && <div className="department-heading">
        <div><h2>{selectedDepartment.name}</h2><p>负责人：{directory.employees.find((item) => item.id === selectedDepartment.leaderId)?.name ?? "未指定"}</p></div>
        <div className="department-actions">
          {isHr && <button className="icon-button" title="编辑组织" aria-label="编辑组织" onClick={() => setEditor({ type: "department", item: selectedDepartment })}><Pencil size={16} /></button>}
          {isHr && <button className="button primary" title={`向${selectedDepartment.name}添加人员`} onClick={() => { setSuccess(""); setEditor({ type: "employee", item: null }); }}><UserPlus size={17} />添加人员</button>}
        </div>
      </div>}
      <div className="directory-toolbar"><label className="directory-search"><Search size={17} /><input aria-label="搜索员工" placeholder="姓名、账号、工号或职位" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
        {isHr && <select aria-label="账号状态筛选" value={status} onChange={(e) => setStatus(e.target.value)}><option value="active">在职 / 启用</option><option value="inactive">已停用</option><option value="">全部状态</option></select>}<span>{employees.length} 人</span></div>
      <div className="employee-table-scroll"><table className="employee-table"><thead><tr><th>员工 / 账号</th><th>组织 / 职位</th><th>直属领导</th><th>权限 / 状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{employees.map((employee) => <tr key={employee.id}><td><button className="employee-name" onClick={() => setDetailId(employee.id)}>{employee.name}<ChevronRight size={14} /></button><small>{employee.username} · {employee.employeeNo}</small></td><td><span>{employee.department}</span><small>{employee.title}</small></td><td>{employee.noManager ? "无直属领导" : directory.employees.find((item) => item.id === employee.managerId)?.name ?? <span className="muted">未指定</span>}</td><td>{employee.roleName}<small className={employee.status === "active" && employee.accountReady ? "account-active" : "account-inactive"}>{employee.status === "inactive" ? "停用" : employee.accountReady ? "已开通登录" : "待设置密码"}</small></td><td>{canManageEmployee(data.currentUser, employee) && <button className="icon-button" title={`编辑${employee.name}`} aria-label={`编辑${employee.name}`} onClick={() => setEditor({ type: "employee", item: employee })}><Pencil size={15} /></button>}</td></tr>)}</tbody></table></div>
      {!employees.length && <div className="empty-state"><Users size={28} /><strong>没有符合条件的员工</strong></div>}
    </section></div>
    {editor?.type === "employee" && <EmployeeEditor actor={data.currentUser} directory={directory} employee={editor.item} initialDepartment={departmentId} initialRole={editor.initialRole} onPositionCreated={(position) => setDirectory((current) => current ? { ...current, positions: [...current.positions.filter((item) => item.id !== position.id), position] } : current)} onRoleCreated={(role) => setDirectory((current) => current ? { ...current, roles: [...current.roles.filter((item) => item.id !== role.id), role] } : current)} onClose={() => setEditor(null)} onSaved={async (employee, password) => {
      setEditor(null); setStatus(employee.status); setDepartmentId(employee.departmentId); setQuery("");
      if (password) setReceipt({ employee, password });
      setSuccess(`${employee.name}的${editor.item ? "资料已更新" : "入职已完成"}。账号：${employee.username}，所属组织：${employee.department}，直属领导：${employee.noManager ? "无直属领导" : directory.employees.find((item) => item.id === employee.managerId)?.name ?? "未指定"}。`);
      try { await refresh(); setError(""); } catch (cause) { setError(cause instanceof Error ? `资料已保存，刷新失败：${cause.message}` : "资料已保存，请刷新页面"); }
    }} />}
    {editor?.type === "department" && <DepartmentEditor actor={data.currentUser} directory={directory} department={editor.item} parentId={departmentId || null} onClose={() => setEditor(null)} onSaved={async (department) => {
      setEditor(null); setDepartmentId(department.id); setSuccess(`组织「${department.name}」已保存。`);
      try { await refresh(); setError(""); } catch (cause) { setError(cause instanceof Error ? `组织已保存，刷新失败：${cause.message}` : "组织已保存，请刷新页面"); }
    }} />}
    {detailId && <Dialog title="员工档案" onClose={() => setDetailId(null)}>{detail ? <><dl className="employee-profile">
      <div><dt>姓名</dt><dd>{detail.employee.name}</dd></div><div><dt>账号</dt><dd>{detail.employee.username}</dd></div><div><dt>工号</dt><dd>{detail.employee.employeeNo}</dd></div><div><dt>入职日期</dt><dd>{detail.employee.hiredAt ?? "未登记"}</dd></div>
      <div><dt>职位名称</dt><dd>{detail.employee.title}</dd></div><div><dt>权限角色</dt><dd>{detail.employee.roleName}</dd></div>
      <div><dt>账号状态</dt><dd>{detail.employee.status === "inactive" ? "停用" : detail.employee.accountReady ? "已开通登录" : "待设置密码"}</dd></div>
      <div><dt>直属领导</dt><dd>{detail.employee.noManager ? "无直属领导" : detail.profile.manager?.name ?? "未指定"}</dd></div>
      <div className="wide"><dt>请假审批人</dt><dd>{detail.profile.leaveApprover?.name ?? "未指定"}{detail.profile.approvalIssue && <small className="approval-issue">{detail.profile.approvalIssue}</small>}</dd></div>
      <div className="wide"><dt>所属组织</dt><dd>{detail.profile.departmentPath.map((item) => item.name).join(" / ")}</dd></div><div className="wide"><dt>汇报关系</dt><dd>{[detail.employee.name, ...detail.profile.managementChain.map((item) => item.name)].join(" → ")}</dd></div>
      <div className="wide"><dt>直属下属</dt><dd>{detail.profile.directReports.map((item) => item.name).join("、") || "无"}</dd></div>
      </dl>{canManageEmployee(data.currentUser, detail.employee) && detail.employee.id !== data.currentUser.id && detail.employee.status === "active" && <div className="account-password-actions"><button className="button ghost" onClick={() => { setResetEmployee(detail.employee); setDetailId(null); }}><KeyRound size={16} />{detail.employee.accountReady ? "重置密码" : "开通登录账号"}</button></div>}{isHr && <History actions={detail.actions} directory={directory} />}</> : <p role={detailError ? "alert" : "status"}>{detailError || "正在加载员工档案…"}</p>}</Dialog>}
    {resetEmployee && <PasswordReset employee={resetEmployee} onClose={() => setResetEmployee(null)} onSaved={(password) => {
      setReceipt({ employee: resetEmployee, password }); setResetEmployee(null);
      void refresh().catch(() => setError("密码已保存，请刷新页面"));
    }} />}
    {receipt && <Dialog title="账号已分配" onClose={() => setReceipt(null)}><div className="account-receipt">
      <dl><div><dt>员工</dt><dd>{receipt.employee.name}</dd></div><div><dt>账号</dt><dd>{receipt.employee.username}</dd></div></dl>
      <PasswordField label="初始密码" value={receipt.password} readOnly />
      <p>请将账号和初始密码私下交付给员工本人。关闭后无法查看此密码；员工首次登录须修改密码。</p>
      <button className="button primary" onClick={() => setReceipt(null)}><CheckCircle2 size={17} />完成</button>
    </div></Dialog>}
  </div>;
}
