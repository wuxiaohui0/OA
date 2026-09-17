import { useEffect, useRef, useState, type FormEvent } from "react";
import { LoaderCircle, X } from "lucide-react";
import { API_BASE, api, pilotApi, leaveTypeLabels, statusLabels, type LeaveDetail, type LeaveRequest, type LeaveFormInput, type User } from "./api";

export const dateTime = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
export const localTime = (value: string) => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)).replace(" ", "T");
const cancelLabels = { pending: "销假待审批", approved: "销假已通过", rejected: "销假已驳回", withdrawn: "销假已撤回" };
const labels: Record<string, string> = { create: "创建草稿", submit: "提交申请", approve: "审批通过", reject: "审批驳回", withdraw: "撤回申请", delete: "隐藏申请", edit: "修改申请", reopen: "重新编辑", request_information: "退回补充", transfer: "审批交接", upload_material: "上传材料", remove_material: "移除未提交材料", remind: "催办", cancel_request: "申请销假", cancel_approved: "批准销假", cancel_rejected: "驳回销假", cancel_withdrawn: "撤回销假", cancel_complete: "全部销假", recover: "恢复流程", escalate: "转人工审核", validate: "制度检查", start_agent_review: "AI 辅助审核", route_to_human: "转人工处理" };

function changeDescription(value: string) {
  try {
    const changes = JSON.parse(value) as Record<string, { before: unknown; after: unknown }>;
    const fields: Record<string, string> = { leaveType: "假别", startAt: "开始时间", endAt: "结束时间", reason: "原因", handoverUser: "交接人", handoverNotes: "交接说明" };
    return Object.entries(changes).map(([key, change]) => `${fields[key] ?? key}：${change.before || "未填写"} → ${change.after || "未填写"}`).join("；") || "重新核算日历时长并保存";
  } catch { return value; }
}

export default function LeaveDrawer({ item, user, users, onClose, onChanged, onAssistant }: {
  item: LeaveRequest; user: User; users: User[]; onClose: () => void; onChanged: () => Promise<void>; onAssistant?: () => void;
}) {
  const [detail, setDetail] = useState<LeaveDetail>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState<LeaveFormInput>({ ...item });
  const [cancelStart, setCancelStart] = useState(localTime(item.startAt));
  const [cancelEnd, setCancelEnd] = useState(localTime(item.endAt));
  const [cancelReason, setCancelReason] = useState("");
  const [showCancel, setShowCancel] = useState(false);
  const [transferId, setTransferId] = useState("");
  const [target, setTarget] = useState("");
  const dialog = useRef<HTMLElement>(null);
  const [transferReason, setTransferReason] = useState("");
  const current = detail?.leave ?? item;
  const mine = current.applicantId === user.id;
  const editable = mine && ["draft", "need_information", "rejected", "withdrawn"].includes(current.status);
  const awaiting = current.status === "human_reviewing";
  const canDecide = awaiting && current.currentApproverId === user.id && user.permissions.approveLeave;
  const blocked = busy || loading || !detail;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    api.details(user.id, item.id).then(result => { if (active) setDetail(result); })
      .catch(cause => { if (active) setError(cause.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [item.id, user.id, reload]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, onClose]);

  async function run(work: () => Promise<LeaveDetail | void>) {
    if (blocked) return;
    setBusy(true); setError("");
    try {
      const value = await work();
      if (value) setDetail(value);
      else setDetail(await api.details(user.id, item.id));
      try { await onChanged(); } catch { setError("操作已完成，列表刷新失败，请刷新页面查看。"); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  function startEdit() {
    setEdit({ leaveType: current.leaveType, startAt: localTime(current.startAt), endAt: localTime(current.endAt), reason: current.reason, handoverUser: current.handoverUser, handoverNotes: current.handoverNotes });
    setEditing(true);
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const result = await pilotApi.edit(current.id, { ...edit, startAt: edit.startAt + ":00+08:00", endAt: edit.endAt + ":00+08:00", version: current.version });
      setEditing(false); return result;
    });
  }

  return <div className="drawer-backdrop" onMouseDown={() => { if (!busy) onClose(); }}>
    <aside ref={dialog} tabIndex={-1} className="drawer pilot-drawer" role="dialog" aria-modal="true" aria-labelledby="leave-detail-title" onMouseDown={event => event.stopPropagation()} onKeyDown={event => {
      if (event.key !== "Tab") return;
      const nodes = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled)') ?? []);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
      <div className="drawer-header"><div><span className="eyebrow">LEAVE REQUEST</span><h2 id="leave-detail-title">请假申请详情</h2></div><button className="icon-button" onClick={onClose} disabled={busy} aria-label="关闭详情"><X size={20} /></button></div>
      {onAssistant && <button className="button ghost" disabled={busy} onClick={onAssistant}>在对话中办理这条申请</button>}
      <div className="detail-hero"><div><h3>{current.applicantName} · {leaveTypeLabels[current.leaveType]}</h3><p>{current.department}</p></div><span className="status-badge neutral">{statusLabels[current.status]}</span></div>
      <div className="detail-grid">
        <div><span>开始时间</span><strong>{dateTime(current.startAt)}</strong></div><div><span>结束时间</span><strong>{dateTime(current.endAt)}</strong></div>
        <div><span>请假时长</span><strong>{current.durationHours} 小时</strong></div><div><span>当前审批人</span><strong>{current.currentApproverName ?? "无待审批节点"}</strong></div>
        <div className="wide"><span>请假原因</span><strong>{current.reason}</strong></div><div className="wide"><span>工作交接</span><strong>{current.handoverUser || "未填写"} · {current.handoverNotes || "无说明"}</strong></div>
      </div>
      {loading && <p role="status"><LoaderCircle className="spin" size={16} /> 正在加载详情…</p>}
      {error && <div className="form-error" role="alert">{error}<button className="button ghost" onClick={() => setReload(value => value + 1)} disabled={busy}>刷新详情</button></div>}
      {detail && !loading && <>
        {detail.review && <section className="pilot-section ai-advice"><h3>AI 审核建议 <small>仅供审批人参考</small></h3><p>{detail.review.summary}</p><p>{detail.review.reason}</p>
          {detail.review.riskFlags.length > 0 && <ul>{detail.review.riskFlags.map(flag => <li key={flag}>{flag}</li>)}</ul>}
          {detail.review.missingFields.length > 0 && <p>建议补充：{detail.review.missingFields.join("、")}</p>}
          <small>{detail.review.policyReferences.join("；")}</small></section>}

        {editable && <section className="pilot-section"><div className="pilot-toolbar"><h3>修改与重新提交</h3><button className="button ghost" disabled={blocked} onClick={startEdit}>编辑申请</button></div>
          {current.status !== "draft" && <p className="muted">编辑保存后恢复为草稿，确认内容后重新提交。此前的审批记录会保留。</p>}
          {editing && <form className="pilot-form" onSubmit={saveEdit}><fieldset disabled={blocked}>
            <label>请假类型<select value={edit.leaveType} onChange={e => setEdit({ ...edit, leaveType: e.target.value as LeaveFormInput["leaveType"] })}>{Object.entries(leaveTypeLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
            <label>开始时间<input type="datetime-local" required value={edit.startAt} onChange={e => setEdit({ ...edit, startAt: e.target.value })} /></label>
            <label>结束时间<input type="datetime-local" required value={edit.endAt} onChange={e => setEdit({ ...edit, endAt: e.target.value })} /></label>
            <label>请假原因<textarea required minLength={2} value={edit.reason} onChange={e => setEdit({ ...edit, reason: e.target.value })} /></label>
            <label>工作交接人<input value={edit.handoverUser ?? ""} onChange={e => setEdit({ ...edit, handoverUser: e.target.value })} /></label>
            <label>交接说明<input value={edit.handoverNotes ?? ""} onChange={e => setEdit({ ...edit, handoverNotes: e.target.value })} /></label>
            <div className="pilot-actions"><button className="button primary">保存修改</button><button type="button" className="button ghost" onClick={() => setEditing(false)}>取消编辑</button></div>
          </fieldset></form>}
          {current.status === "draft" && !editing && <button className="button primary" disabled={blocked} onClick={() => run(() => api.submit(user.id, current.id, current.version))}>确认并提交审批</button>}
        </section>}

        <section className="pilot-section"><h3>申请材料 <small>{detail.attachments.length}/10</small></h3>
          {detail.attachments.length === 0 && <p className="muted">尚未上传材料</p>}
          {detail.attachments.map(file => <div className="material-row" key={file.id}><a href={`${API_BASE}/api/attachments/${file.id}`} download>{file.filename}</a><span>{Math.ceil(file.size / 1024)} KB</span>{editable && current.status === "draft" && !detail.actions.some(a => a.action === "submit") && <button className="text-button" disabled={blocked} onClick={() => run(() => pilotApi.removeAttachment(file.id))}>移除</button>}</div>)}
          {editable && <label className="upload-label">上传证明材料<input aria-label="上传证明材料" type="file" accept=".pdf,.png,.jpg,.jpeg" disabled={blocked} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) { if (file.size > 5 * 1024 * 1024) setError("文件不能超过 5 MB"); else void run(() => pilotApi.upload(current.id, file)); } }} /><small>PDF、PNG 或 JPG，单份不超过 5 MB。提交后如需补交，请先撤回或由审批人退回补充。</small></label>}
        </section>

        {canDecide && <section className="pilot-section"><h3>人工审批</h3><label className="pilot-label">审批意见<textarea aria-label="审批意见" placeholder="退回补充或驳回时请写明原因" value={reason} onChange={e => setReason(e.target.value)} disabled={blocked} /></label>
          <div className="pilot-actions"><button className="button primary" disabled={blocked} onClick={() => run(() => api.approve(user.id, current.id, reason || "同意", current.version))}>批准</button>
            <button className="button ghost" disabled={blocked || reason.trim().length < 2} onClick={() => run(() => pilotApi.action(current.id, "request-information", { reason, version: current.version }))}>退回补充</button>
            <button className="button danger-outline" disabled={blocked || reason.trim().length < 2} onClick={() => run(() => api.reject(user.id, current.id, reason, current.version))}>驳回</button></div>
        </section>}

        {mine && ["submitted", "validating", "agent_reviewing", "human_reviewing", "need_information"].includes(current.status) && <section className="pilot-section"><h3>申请操作</h3>
          <label className="pilot-label">撤回原因<input value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：时间调整，修改后重新提交" disabled={blocked} /></label>
          <div className="pilot-actions"><button className="button ghost" disabled={blocked || reason.trim().length < 2} onClick={() => run(() => pilotApi.action(current.id, "withdraw", { reason, version: current.version }))}>撤回申请</button>
            {awaiting && <button className="button ghost" disabled={blocked} onClick={() => run(() => pilotApi.remind(current.id))}>提醒审批人</button>}</div></section>}

        {awaiting && (canDecide || user.permissions.manageOrganization) && <section className="pilot-section"><h3>审批交接</h3><p className="muted">转交当前待办，员工今后的默认审批人可在组织与人员中调整。</p><button className="button ghost" disabled={blocked} onClick={() => { setTransferId("leave"); setTarget(""); }}>转交请假审批</button></section>}

        {(current.status === "approved" || detail.cancellations.length > 0) && <section className="pilot-section"><div className="pilot-toolbar"><h3>销假与额度返还</h3>{mine && current.status === "approved" && <button className="button ghost" disabled={blocked} onClick={() => setShowCancel(!showCancel)}>申请销假</button>}</div>
          <p className="muted">已返还 {detail.cancellations.filter(c => c.status === "approved").reduce((sum, c) => sum + c.hours, 0)} 小时。销假需人工批准，按原申请的工作时段返还。</p>
          {showCancel && <form className="pilot-form" onSubmit={event => { event.preventDefault(); void run(async () => { const value = await pilotApi.cancel(current.id, { startAt: cancelStart + ":00+08:00", endAt: cancelEnd + ":00+08:00", reason: cancelReason, version: current.version }); setShowCancel(false); return value; }); }}><fieldset disabled={blocked}>
            <label>销假开始<input type="datetime-local" value={cancelStart} onChange={e => setCancelStart(e.target.value)} required /></label><label>销假结束<input type="datetime-local" value={cancelEnd} onChange={e => setCancelEnd(e.target.value)} required /></label>
            <label>销假原因<textarea required minLength={2} value={cancelReason} onChange={e => setCancelReason(e.target.value)} /></label><button className="button primary">提交销假申请</button>
          </fieldset></form>}
          {detail.cancellations.map(c => <div className="cancellation-card" key={c.id}><strong>{cancelLabels[c.status]} · {c.hours} 小时</strong><p>{dateTime(c.startAt)} — {dateTime(c.endAt)}</p><p>{c.reason}</p><small>审批人：{c.approverName}{c.comment ? ` · ${c.comment}` : ""}</small>
            {c.status === "pending" && <>
              {c.approverId === user.id && <><label className="pilot-label">销假审批意见<textarea value={reason} onChange={e => setReason(e.target.value)} disabled={blocked} /></label><div className="pilot-actions"><button className="button primary" disabled={blocked} onClick={() => run(() => pilotApi.cancelAction(c.id, "approve", { reason: reason || "同意销假" }))}>批准销假</button><button className="button danger-outline" disabled={blocked || reason.trim().length < 2} onClick={() => run(() => pilotApi.cancelAction(c.id, "reject", { reason }))}>驳回销假</button></div></>}
              {mine && <button className="button ghost" disabled={blocked} onClick={() => run(() => pilotApi.cancelAction(c.id, "withdraw", { reason: "申请人撤回销假" }))}>撤回销假</button>}
              {(c.approverId === user.id || user.permissions.manageOrganization) && <button className="button ghost" disabled={blocked} onClick={() => { setTransferId(c.id); setTarget(""); }}>转交销假审批</button>}
            </>}
          </div>)}
        </section>}

        {transferId && <form className="pilot-section pilot-form" onSubmit={event => { event.preventDefault(); void run(async () => { const body = { approverId: target, reason: transferReason, version: current.version }; const value = transferId === "leave" ? await pilotApi.action(current.id, "transfer", body) : await pilotApi.cancelAction(transferId, "transfer", body); setTransferId(""); return value; }); }}><h3>选择交接审批人</h3><fieldset disabled={blocked}>
          <label>新审批人<select required value={target} onChange={e => setTarget(e.target.value)}><option value="">请选择</option>{users.filter(u => u.status === "active" && u.permissions.approveLeave && u.id !== current.applicantId && u.id !== (transferId === "leave" ? current.currentApproverId : detail.cancellations.find(c => c.id === transferId)?.approverId)).map(u => <option key={u.id} value={u.id}>{u.name} · {u.department}</option>)}</select></label>
          <label>交接原因<textarea required minLength={2} value={transferReason} onChange={e => setTransferReason(e.target.value)} /></label><div className="pilot-actions"><button className="button primary">确认转交</button><button type="button" className="button ghost" onClick={() => setTransferId("")}>取消</button></div>
        </fieldset></form>}

        <section className="pilot-section"><h3>审批处理记录</h3>{detail.approvalSteps.length === 0 && <p className="muted">提交后生成审批记录</p>}{detail.approvalSteps.map(step => <div className="material-row" key={step.id}><div><strong>{step.approverName} · 第 {step.stepOrder} 次处理</strong><p>{step.comment ?? "等待审批"}</p>{step.reviewedAt && <small>{dateTime(step.reviewedAt)}</small>}</div><span>{({ pending: "待处理", approved: "已批准", rejected: "已驳回", skipped: "已退回 / 转交 / 撤回" })[step.status]}</span></div>)}</section>
        <section className="pilot-section"><h3>完整流转记录</h3><div className="timeline">{detail.actions.map(action => <div className="timeline-item" key={action.id}><span className={`timeline-dot ${action.actorType}`} /><div><strong>{action.actorName} · {labels[action.action] ?? action.action}</strong><p>{action.action === "edit" ? changeDescription(action.reason) : action.reason}</p><time>{dateTime(action.createdAt)}</time></div></div>)}</div></section>
        {mine && ["draft", "rejected", "withdrawn"].includes(current.status) && <button className="button danger-outline" disabled={blocked} onClick={() => { if (window.confirm("隐藏这条申请？操作和历史审批记录会保留。")) void run(async () => { await api.deleteLeave(user.id, current.id); await onChanged(); onClose(); }); }}>隐藏申请</button>}
      </>}
    </aside>
  </div>;
}
