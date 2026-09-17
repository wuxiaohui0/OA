import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bot, Check, LoaderCircle, MessageSquarePlus, Paperclip, Send, ShieldCheck, Sparkles } from "lucide-react";
import { api, API_BASE, ApiError, leaveTypeLabels, statusLabels, type AssistantAction, type ChatWorkspace, type ChatCard, type ChatCatalog, type ChatSchema, type User } from "./api";
import "./assistant.css";

function time(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function DataCard({ card, send, disabled }: { card: ChatCard; send: (text: string) => void; disabled: boolean }) {
  return <section className="oa-data-card"><h3>{card.title}</h3>
    {card.kind === "requests" && <><small>共 {card.total} 条，最多显示 50 条；点击选择后继续办理</small>
      {card.items.length === 0 && <p>暂无记录。</p>}
      {card.items.map(item => <button disabled={disabled} className="oa-record" key={item.id} onClick={() => send("选择申请:" + item.id)}>
        <strong>{item.applicantName} · {leaveTypeLabels[item.leaveType]} · {statusLabels[item.status]}</strong>
        <span>{time(item.startAt)} → {time(item.endAt)} · {item.durationHours} 小时</span><span>{item.reason}</span><small>{item.id}</small>
      </button>)}</>}
    {card.kind === "request" && <><p>{card.leave.applicantName} · {statusLabels[card.leave.status]} · {card.leave.durationHours} 小时</p>
      <p>{card.leave.reason}</p><p>审批人：{card.leave.currentApproverName || "提交时分配"}</p>
      {card.review && <p>辅助建议：{card.review.reason} {card.review.riskFlags.join("；")}</p>}
      {card.actions.slice(-10).map(a => <p key={a.id}>{time(a.createdAt)} · {a.actorName} · {a.reason}</p>)}
    </>}
    {card.kind === "cancellations" && <>{card.items.length === 0 && <p>暂无销假待办。</p>}{card.items.map(c => <button className="oa-record" disabled={disabled} key={c.id} onClick={() => send("选择销假:" + c.id)}>
      <strong>{c.applicantName} · 销假 {c.hours} 小时</strong><span>{time(c.startAt)} → {time(c.endAt)}</span><span>{c.reason}</span>
    </button>)}</>}
    {card.kind === "ledger" && <>{card.balances.map(b => <p key={b.leaveType}>{leaveTypeLabels[b.leaveType]}：剩余 <strong>{b.remainingHours}</strong> / {b.totalHours} 小时</p>)}
      <a href={API_BASE + "/api/balance-ledger/" + encodeURIComponent(card.employeeId) + "/export"}>下载台账 CSV</a>
      {card.entries.slice(0, 30).map(e => <p key={e.id}>{time(e.createdAt)} · {leaveTypeLabels[e.leaveType]} · 总额 {e.totalDelta > 0 ? "+" : ""}{e.totalDelta} / 已用 {e.usedDelta > 0 ? "+" : ""}{e.usedDelta} 小时<br />{e.reason} · {e.actorName}</p>)}
    </>}
    {card.kind === "calendar" && <><p>默认周一至周五，09:00–12:00、13:30–18:00。以下设置优先：</p>{card.days.length === 0 && <p>暂无特殊日期。</p>}{card.days.map(d => <p key={d.day}>{d.day} · {d.isWorkday ? "工作日" : "休息日"} · {d.name}</p>)}</>}
    {card.kind === "notifications" && <><small>{card.unread} 条未读</small>{card.items.length === 0 && <p>暂无通知。</p>}{card.items.map(n => <div key={n.id} className="oa-record"><strong>{n.readAt ? "" : "● "}{n.title}</strong><span>{n.body}</span>{n.requestId && <button disabled={disabled} className="button ghost" onClick={() => send("选择申请:" + n.requestId)}>查看申请</button>}</div>)}</>}
    {card.kind === "organization" && <>{card.employees.map(u => <p key={u.id}>{u.name} · {u.employeeNo} · {u.department} · {u.title} · {u.roleName}<br /><small>{u.id}</small></p>)}</>}
    {card.kind === "employee" && <><p>{card.employee.name} · {card.employee.department} · {card.employee.title} · {card.employee.roleName}</p><p>直属领导：{card.profile.manager?.name || "未设置"}，请假审批人：{card.profile.leaveApprover?.name || "未设置"}</p></>}
  </section>;
}

const optionNames: Record<string, string> = { annual: "年假", personal: "事假", sick: "病假", active: "启用", inactive: "停用" };
const permissionNames: Record<string, string> = { manageOrganization: "管理组织人员", manageAccounts: "管理管理员账号", leadTeam: "担任团队负责人", approveLeave: "审批请假" };

function resolve(schema: ChatSchema, root: ChatSchema): ChatSchema {
  if (schema.$ref) return root.$defs?.[schema.$ref.split("/").pop()!] || schema;
  return schema.anyOf ? resolve(schema.anyOf.find(s => s.type !== "null") || schema.anyOf[0], root) : schema;
}

function LeaveActionEditor({ data, busy, save, close }: {
  data: NonNullable<AssistantAction["editableData"]>; busy: boolean;
  save: (data: NonNullable<AssistantAction["editableData"]>) => Promise<void>; close: () => void;
}) {
  const localTime = (value: string) => new Date(new Date(value).getTime() + 8 * 3600 * 1000).toISOString().slice(0, 16);
  const [values, setValues] = useState({ ...data, startAt: localTime(data.startAt), endAt: localTime(data.endAt) });
  return <form className="oa-operation-form oa-preview-editor" aria-label="修改待确认请假" onSubmit={e => {
    e.preventDefault();
    void save({ ...values, startAt: values.startAt + ":00+08:00", endAt: values.endAt + ":00+08:00" });
  }}>
    <strong>修改待确认信息</strong>
    <label>请假类型<select aria-label="请假类型" value={values.leaveType} disabled={busy} onChange={e => setValues(v => ({ ...v, leaveType: e.target.value as typeof v.leaveType }))}>
      {Object.entries(leaveTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    <div className="oa-editor-dates">{(["startAt", "endAt"] as const).map(key => <label key={key}>{key === "startAt" ? "开始时间" : "结束时间"}<input type="datetime-local" required disabled={busy} value={values[key]} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} /></label>)}</div>
    <label>请假原因<input required minLength={2} disabled={busy} value={values.reason} onChange={e => setValues(v => ({ ...v, reason: e.target.value }))} /></label>
    <label>工作交接人<input disabled={busy} value={values.handoverUser || ""} onChange={e => setValues(v => ({ ...v, handoverUser: e.target.value || null }))} /></label>
    <label>交接说明<input disabled={busy} value={values.handoverNotes || ""} onChange={e => setValues(v => ({ ...v, handoverNotes: e.target.value || null }))} /></label>
    <small>更新后请再次核对确认卡片，此处不会执行业务变更。</small>
    <div className="preview-actions"><button className="button ghost" type="button" disabled={busy} onClick={close}>放弃修改</button><button className="button primary" disabled={busy}>更新确认卡片</button></div>
  </form>;
}

function OperationForm({ catalog, workspace, busy, prepare }: {
  catalog: ChatCatalog; workspace: ChatWorkspace | null; busy: boolean;
  prepare: (operation: string, target: string | null, data: Record<string, unknown>) => Promise<void>;
}) {
  const [op, setOp] = useState(catalog.items[0].operation);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [target, setTarget] = useState("");
  const operation = catalog.items.find(i => i.operation === op) || catalog.items[0];
  useEffect(() => { if (!catalog.items.some(i => i.operation === op)) { setOp(catalog.items[0].operation); setValues({}); setTarget(""); } }, [catalog, op]);
  const isEdit = op === "edit_leave" || op === "update_employee";
  const context = workspace?.context;
  const selectedTarget = target || (operation.target === "leave" ? context?.leave.id : "") || "";
  const setValue = (key: string, value: unknown) => setValues(v => ({ ...v, [key]: value }));

  function field(key: string, raw: ChatSchema, required: boolean, path = key) {
    const schema = resolve(raw, operation.schema);
    const label = catalog.labels[key] || permissionNames[key] || optionNames[key] || key;
    if (schema.type === "object") return <fieldset key={path}><legend>{label}</legend>{Object.entries(schema.properties || {}).map(([k, v]) => field(k, v, (schema.required || []).includes(k), path + "." + k))}</fieldset>;
    const fallback = raw.default !== undefined ? raw.default : schema.default;
    const value = values[path] !== undefined ? values[path] : isEdit ? "" : fallback === undefined ? "" : fallback;
    const nullable = raw.anyOf?.some(s => s.type === "null");
    let options: { value: string; label: string }[] | null = null;
    if (schema.const !== undefined) options = [{ value: String(schema.const), label: optionNames[String(schema.const)] || String(schema.const) }];
    else if (schema.enum) options = schema.enum.map(v => ({ value: String(v), label: optionNames[String(v)] || String(v) }));
    else if (["approverId", "userId", "managerId", "leaveApproverId", "leaderId"].includes(key)) options = catalog.directory.employees.filter(u => u.status === "active").map(u => ({ value: u.id, label: u.name + " · " + u.employeeNo }));
    else if (["departmentId", "parentId"].includes(key)) options = catalog.directory.departments.map(d => ({ value: d.id, label: d.name }));
    else if (key === "role") options = catalog.directory.roles.map(r => ({ value: r.id, label: r.name }));
    else if (key === "title") options = catalog.directory.positions.map(p => ({ value: p.name, label: p.name }));
    else if (schema.type === "boolean") options = [{ value: "true", label: "是" }, { value: "false", label: "否" }];
    const display = value === null && options ? "__null__" : String(value ?? "");
    return <label key={path}><span>{label}{required ? " *" : ""}</span>{options ?
      <select aria-label={label} required={required} value={display} onChange={e => setValue(path, e.target.value === "" ? "" : e.target.value === "__null__" ? null : schema.type === "boolean" ? e.target.value === "true" : e.target.value)}>
        <option value="">{isEdit ? "保持不变" : "请选择"}</option>{nullable && <option value="__null__">不设置</option>}{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select> :
      <input aria-label={label} required={required && value !== null} disabled={value === null} type={key === "startAt" || key === "endAt" ? "datetime-local" : key === "day" || key === "hiredAt" ? "date" : schema.type === "number" ? "number" : "text"}
        step={schema.type === "number" ? "0.01" : undefined} min={schema.minimum} max={schema.maximum}
        value={display} placeholder={isEdit ? "留空保持不变" : ""} onChange={e => setValue(path, schema.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} />}
      {nullable && !options && <span><input aria-label={"不设置" + label} type="checkbox" checked={value === null} onChange={e => setValue(path, e.target.checked ? null : "")} />不设置{label}</span>}
    </label>;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const data: Record<string, unknown> = {};
    // Apply declared defaults, including nested entitlement and permission forms.
    function collect(props: Record<string, ChatSchema>, prefix = "") {
      Object.entries(props).forEach(([key, raw]) => {
        const path = prefix ? prefix + "." + key : key;
        const schema = resolve(raw, operation.schema);
        if (schema.type === "object") { collect(schema.properties || {}, path); return; }
        let value = values[path];
        if (value === undefined && !isEdit) value = raw.default !== undefined ? raw.default : schema.default;
        if (value === "" || value === undefined) return;
        if ((key === "startAt" || key === "endAt") && typeof value === "string") value += ":00+08:00";
        const parts = path.split(".");
        let destination = data;
        parts.slice(0, -1).forEach(p => { destination[p] ??= {}; destination = destination[p] as Record<string, unknown>; });
        destination[parts[parts.length - 1]] = value;
      });
    }
    collect(operation.schema.properties || {});
    await prepare(op, selectedTarget || null, data);
  }
  return <form className="oa-operation-form" onSubmit={e => void submit(e)}>
    <label><span>办理事项</span><select aria-label="办理事项" value={op} disabled={busy} onChange={e => { setOp(e.target.value); setValues({}); setTarget(""); }}>
      {catalog.items.map(o => <option key={o.operation} value={o.operation}>{o.title}</option>)}
    </select></label>
    {operation.target && <label><span>{operation.target === "leave" ? "申请编号（可先在卡片选择）" : "操作对象"}{operation.target === "department" ? "（不选则新建）" : ""}</span>
      {["employee", "department", "cancellation", "attachment"].includes(operation.target) ?
        <select value={selectedTarget} required={operation.target !== "department"} onChange={e => setTarget(e.target.value)}>
          <option value="">{operation.target === "department" ? "新建部门" : "请选择"}</option>
          {operation.target === "employee" && catalog.directory.employees.map(u => <option key={u.id} value={u.id}>{u.name} · {u.employeeNo}</option>)}
          {operation.target === "department" && catalog.directory.departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
          {operation.target === "cancellation" && context?.cancellations.map(c => <option key={c.id} value={c.id}>{c.hours} 小时 · {c.reason} · {c.status}</option>)}
          {operation.target === "attachment" && context?.attachments.map(a => <option key={a.id} value={a.id}>{a.filename}</option>)}
        </select> : <input required value={selectedTarget} type={operation.target === "day" ? "date" : "text"} onChange={e => setTarget(e.target.value)} />}
    </label>}
    {Object.entries(operation.schema.properties || {}).map(([k, s]) => field(k, s, (operation.schema.required || []).includes(k)))}
    <button className="button primary" disabled={busy}>生成操作预览</button>
  </form>;
}

export default function Assistant({ user, contextId, onContextUsed, onChanged }: { user: User; contextId?: string; onContextUsed: () => void; onChanged: () => Promise<void> }) {
  const userId = user.id;
  const [workspace, setWorkspace] = useState<ChatWorkspace | null>(null);
  const [catalog, setCatalog] = useState<ChatCatalog | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [password, setPassword] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const chatBody = useRef<HTMLDivElement>(null);
  const messageInput = useRef<HTMLInputElement>(null);
  const storageKey = "oa-unified-conversation:" + userId;
  const live = useRef(true);
  const selectingContext = useRef(false);
  function show(value: ChatWorkspace) {
    if (!live.current) return;
    setWorkspace(value);
    localStorage.setItem(storageKey, value.id);
  }
  useEffect(() => {
    live.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function restore() {
      try {
        const nextCatalog = await api.chatCatalog();
        if (cancelled) return;
        setCatalog(nextCatalog);
        const id = localStorage.getItem(storageKey);
        if (id) {
          const value = await api.chatWorkspace(userId, id);
          if (cancelled) return;
          show(value); setBusy(value.busy);
          if (value.busy) timer = setTimeout(() => void restore(), 1500);
        } else setBusy(false);
      } catch (cause) {
        if (cancelled) return;
        if (cause instanceof ApiError && cause.status === 404) localStorage.removeItem(storageKey);
        else setError(cause instanceof Error ? cause.message : "恢复对话失败");
        setBusy(false);
      }
    }
    void restore();
    return () => { cancelled = true; live.current = false; clearTimeout(timer); };
  }, [userId, user.permissions.approveLeave, user.permissions.manageOrganization]);
  useEffect(() => {
    if (!contextId || busy || selectingContext.current) return;
    selectingContext.current = true;
    void run(async () => (await api.agentMessage(userId, "选择申请:" + contextId, workspace?.id)).conversation)
      .finally(() => { selectingContext.current = false; onContextUsed(); });
  }, [contextId, busy]);
  useEffect(() => { if (chatBody.current) chatBody.current.scrollTop = chatBody.current.scrollHeight; }, [workspace, busy, error]);
  async function run(action: () => Promise<ChatWorkspace>) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await action(); show(result); setPassword(""); await onChanged();
      if (result.action?.status === "succeeded") {
        const nextCatalog = await api.chatCatalog();
        if (live.current) setCatalog(nextCatalog);
      }
      return result;
    }
    catch (cause) {
      setError(cause instanceof Error ? cause.message : "请求失败，请刷新对话核对状态后重试");
      if (workspace?.id) { try { show(await api.chatWorkspace(userId, workspace.id)); } catch { /* preserve original error */ } }
    } finally { if (live.current) setBusy(false); }
  }
  async function send(text: string) {
    if (!text.trim()) return;
    if (editingId) { setError("请先更新确认卡片或放弃修改，再继续对话。"); return; }
    const result = await run(async () => (await api.agentMessage(userId, text.trim(), workspace?.id, undefined, workspace?.action?.id)).conversation);
    if (result) setMessage("");
  }
  const current = workspace?.context;
  const ownRequest = current?.leave.applicantId === userId;
  const canUpload = ownRequest && ["draft", "need_information", "rejected", "withdrawn"].includes(current!.leave.status);
  const contextShortcuts = [
    ...(ownRequest && current!.leave.status === "draft" ? ["提交这条"] : []),
    ...(ownRequest && current!.leave.status === "human_reviewing" ? ["催办这条"] : []),
    ...(current?.leave.currentApproverId === userId && !ownRequest && user.permissions.approveLeave && current.leave.status === "human_reviewing" ? ["批准这条", "退回补充"] : []),
    "查看详情",
  ];
  const action = workspace?.action;
  const pending = action?.status === "pending";
  const editing = pending && editingId === action.id;
  useEffect(() => { setEditingId(null); }, [action?.id, action?.status]);
  const confirmLabels: Record<string, string> = {
    apply_leave: "确认请假并提交",
    create_leave: "确认创建草稿", edit_leave: "确认修改", submit: "确认提交审批",
    approve: "确认批准", reject: "确认驳回", request_information: "确认退回补充",
    withdraw: "确认撤回", delete_leave: "确认删除", cancel_leave: "确认申请销假",
    approve_cancellation: "确认批准销假", reject_cancellation: "确认驳回销假",
  };
  const actionStatus: Record<string, string> = { pending: "待确认", executing: "执行中", succeeded: "已完成", failed: "执行失败", cancelled: "已取消" };
  return <section className="assistant-card oa-assistant">
    <div className="assistant-heading"><div className="bot-mark"><Sparkles size={19} /></div><div><h2>智能 OA 助手</h2><p>{user.name} · {user.roleName} · 按你的权限办理业务</p></div>
      <button className="icon-button" aria-label="刷新对话" disabled={busy || !workspace} onClick={() => void run(() => api.chatWorkspace(userId, workspace!.id))}>↻</button>
      <button className="icon-button" title={action?.status === "pending" ? "请先确认或取消当前操作" : "新对话"} aria-label="新对话" disabled={busy || action?.status === "pending"} onClick={() => { localStorage.removeItem(storageKey); setWorkspace(null); setMessage(""); setError(""); setPassword(""); }}><MessageSquarePlus size={18} /></button>
    </div>
    <div className="oa-shortcuts">{["我的申请", ...(user.permissions.approveLeave ? ["待我审批"] : []), "我的余额", "工作日历", "消息通知", "通讯录"].map(t => <button disabled={busy} key={t} onClick={() => void send(t)}>{t}</button>)}</div>
    <div className="chat-body" ref={chatBody} role="log" aria-label="助手对话" aria-live="polite">
      {!workspace?.messages.length && <div className="chat-message agent-message"><div className="chat-avatar"><Bot size={16} /></div><div>你好，可以说“明天下午请事假，原因：家中有事”，或先查询申请再继续办理。需要补充材料时，可以直接上传。</div></div>}
      {workspace?.messages.map((m, i) => <div className={"chat-message " + (m.role === "user" ? "user-message" : "agent-message")} key={i}>{m.role === "assistant" && <div className="chat-avatar"><Bot size={16} /></div>}<div>{m.content}</div></div>)}
      {workspace?.cards.map((card, i) => <DataCard key={i} card={card} send={text => void send(text)} disabled={busy} />)}
      {action && <section className="oa-data-card oa-action"><div className="draft-title"><strong>{action.preview.title}</strong><span>{actionStatus[action.status]}</span></div>
        <dl>{action.preview.fields.map((f, i) => <div key={i}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl>
        {action.status === "pending" && <><p>{action.preview.note}</p>
          {action.requiresPassword && <label className="oa-secret">初始密码<input aria-label="初始密码" type="password" autoComplete="new-password" minLength={12} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /><small>至少 12 位，通过专用字段提交，不进入对话或模型。</small></label>}
          <p className="oa-action-hint">核对以上信息后，使用对话下方的确认按钮。</p>
        </>}
        {action.result && <p role="status">{action.result.message}</p>}
      </section>}
      {busy && <div className="chat-message agent-message"><div className="chat-avatar"><LoaderCircle className="spin" size={16} /></div><div>正在处理，请稍候…</div></div>}
      {error && <div className="chat-error" role="alert">{error}</div>}
    </div>
    {workspace?.clarification && !pending && <section className="oa-clarification" aria-label="待补充信息">
      <strong>请补充信息</strong><p>{workspace.clarification.missingFields.join("、") || workspace.clarification.message}</p>
      <div className="oa-shortcuts">{workspace.clarification.choices.map(choice => <button key={choice} disabled={busy} onClick={() => void send(choice)}>{choice}</button>)}
        {!workspace.clarification.choices.length && <button disabled={busy} onClick={() => messageInput.current?.focus()}>补充信息</button>}</div>
      <small>补齐后会生成确认卡片，当前尚未执行业务变更。</small>
    </section>}
    {editing && action.editableData && workspace && <LeaveActionEditor key={action.id} data={action.editableData} busy={busy} close={() => setEditingId(null)} save={async data => {
      const result = await run(() => api.prepareChat(workspace.id, action.operation, action.targetId, data));
      if (result) setEditingId(null);
    }} />}
    {pending && <section className="oa-confirmation" aria-label="人工确认">
      <div><strong><ShieldCheck size={16} />待你确认 · {action.preview.title}</strong>
        <p>{editing ? "正在修改，请更新卡片后再确认。" : action.operation === "apply_leave" ? "确认后直接提交给审批人，等待审批结果。" : action.operation === "create_leave" ? "本次仅保存草稿。" : "请核对上方操作详情，点击确认后才会执行。"}</p></div>
      <div className="preview-actions">
        {action.editableData && <button className="button ghost" disabled={busy || editing} onClick={() => { setFormOpen(false); setEditingId(action.id); }}>修改信息</button>}
        <button className="button ghost" disabled={busy} onClick={() => void run(() => api.cancelAgentAction(userId, action.id))}>取消操作</button>
        <button className="button primary" disabled={busy || editing || (action.requiresPassword && password.length < 12)} onClick={() => void run(() => api.confirmAgentAction(userId, action.id, password || undefined))}><Check size={16} />{confirmLabels[action.operation] || "确认执行"}</button>
      </div>
    </section>}
    {current && <section className="oa-context"><strong>当前申请：{current.leave.applicantName} · {leaveTypeLabels[current.leave.leaveType]} · {statusLabels[current.leave.status]}</strong>
      <span>{time(current.leave.startAt)} → {time(current.leave.endAt)} · {current.leave.durationHours} 小时 · {current.leave.reason}</span>
      <div className="oa-shortcuts">{contextShortcuts.map(t => <button disabled={busy} key={t} onClick={() => void send(t)}>{t}</button>)}
        {canUpload && <label className="oa-upload"><Paperclip size={14} />上传材料<input aria-label="上传材料" type="file" accept=".pdf,.png,.jpg,.jpeg" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ""; if (!file || !workspace) return;
          if (file.size > 5 * 1024 * 1024) { setError("文件不能超过 5 MB"); return; }
          void run(() => api.chatUpload(workspace.id, file));
        }} /></label>}
      </div>
      {current.attachments.map(a => <a key={a.id} href={API_BASE + "/api/attachments/" + encodeURIComponent(a.id)}>{a.filename}</a>)}
      {current.cancellations.map(c => <button className="oa-cancellation" disabled={busy} key={c.id} onClick={() => void send("选择销假:" + c.id)}>销假 {c.hours} 小时 · {({ pending: "待审批", approved: "已批准", rejected: "已驳回", withdrawn: "已撤回" })[c.status]} · {c.reason}</button>)}
    </section>}
    <form className="chat-input" onSubmit={e => { e.preventDefault(); void send(message); }}><input ref={messageInput} aria-label="消息" maxLength={4000} value={message} disabled={busy || editing} onChange={e => setMessage(e.target.value)} placeholder={user.permissions.approveLeave ? "例如：原因改为家中有事 / 批准这条 / 确认执行" : "例如：明天下午请事假，原因：家中有事"} /><button aria-label="发送" disabled={busy || editing || !message.trim()}><Send size={18} /></button></form>
    <div className="oa-form-toggle"><button disabled={busy || editing || !catalog} onClick={() => setFormOpen(v => !v)}>{formOpen ? "收起办理表单" : "办理业务"}</button><span>复杂信息也可直接在这里填写</span></div>
    {formOpen && catalog && <OperationForm catalog={catalog} workspace={workspace} busy={busy} prepare={async (op, target, data) => {
      const result = await run(async () => {
        const id = workspace?.id || (await api.agentMessage(userId, "帮助")).conversation.id;
        return api.prepareChat(id, op, target, data);
      });
      if (result) setFormOpen(false);
    }} />}
    <div className="chat-note"><ShieldCheck size={13} />依据当前账号权限办理；AI 提供建议，业务变更由你确认。</div>
  </section>;
}
