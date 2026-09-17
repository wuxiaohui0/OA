import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { API_BASE, pilotApi, leaveTypeLabels, statusLabels, type BootstrapData, type CalendarDay, type LedgerData, type LeaveRequest, type LeaveType, type Notification } from "./api";
import { dateTime, localTime } from "./LeaveDetail";

type Page = "calendar" | "ledger" | "notifications" | "admin";
const titles = { calendar: "工作日历", ledger: "假期台账", notifications: "消息通知", admin: "请假管理" };
const descriptions = { calendar: "查看工作日、节假日和调休安排。时长按北京时间计算。", ledger: "额度、扣减、销假返还与 HR 调整均可追溯。", notifications: "站内消息每 30 秒刷新，打开申请查看详细处理意见。", admin: "查看最近 500 条申请，按部门跟进进度并处理审批交接。" };

export default function PilotPage({ page, data, onChanged, onSelect, onOpen }: {
  page: Page; data: BootstrapData; onChanged: () => Promise<void>; onSelect: (item: LeaveRequest) => void; onOpen: (id: string) => Promise<void>;
}) {
  const admin = data.currentUser.permissions.manageOrganization;
  const today = localTime(new Date().toISOString()).slice(0, 10);
  const [days, setDays] = useState<CalendarDay[]>([]);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [day, setDay] = useState(today);
  const [workday, setWorkday] = useState<boolean>();
  const [name, setName] = useState<string>();
  const [employee, setEmployee] = useState(data.currentUser.id);
  const [ledger, setLedger] = useState<LedgerData>();
  const [type, setType] = useState<LeaveType>("annual");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const adjustment = useRef<{ payload: string; id: string } | undefined>(undefined);
  const [messages, setMessages] = useState<Notification[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [requests, setRequests] = useState<LeaveRequest[]>([]);
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (page === "calendar") setDays((await pilotApi.calendar()).days);
    if (page === "notifications") setMessages((await pilotApi.notifications()).items);
    if (page === "admin" && admin) setRequests((await pilotApi.adminRequests()).items);
  }, [page, admin]);

  useEffect(() => {
    let active = true;
    setLoading(true); setError(""); setLedger(undefined);
    const fetch = async () => {
      try {
        if (page === "ledger") {
          const result = await pilotApi.ledger(employee);
          if (active) setLedger(result);
        } else await load();
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "加载失败"); }
      finally { if (active) setLoading(false); }
    };
    void fetch();
    const timer = setInterval(() => { if (!document.hidden) void fetch(); }, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [load, page, employee]);

  async function run(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await work(); await onChanged(); setNotice("操作已完成"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
    finally { setBusy(false); }
  }

  function selectDay(value: string) {
    setDay(value);
    setWorkday(undefined);
    setName(undefined);
  }

  function adjust(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const body = { userId: employee, leaveType: type, hours: Number(amount), reason };
      const payload = JSON.stringify(body);
      if (adjustment.current?.payload !== payload) adjustment.current = { payload, id: crypto.randomUUID() };
      setLedger(await pilotApi.adjust({ ...body, operationId: adjustment.current.id }));
      adjustment.current = undefined; setAmount(""); setReason("");
    });
  }

  const selectedOverride = days.find(d => d.day === day);
  const selectedWeekday = new Date(day + "T12:00:00+08:00").getUTCDay();
  const selectedWorkday = workday ?? (selectedOverride ? Boolean(selectedOverride.isWorkday) : selectedWeekday !== 0 && selectedWeekday !== 6);
  const selectedName = name ?? selectedOverride?.name ?? "";
  const calendarYear = Number(month.slice(0, 4)), calendarMonth = Number(month.slice(5));
  const monthLength = new Date(Date.UTC(calendarYear, calendarMonth, 0)).getUTCDate();
  const offset = (new Date(Date.UTC(calendarYear, calendarMonth - 1, 1)).getUTCDay() + 6) % 7;
  const departments = [...new Set(requests.map(r => r.department))];

  return <>
    <div className="page-heading"><div><span className="eyebrow">LEAVE PILOT</span><h1>{titles[page]}</h1><p>{descriptions[page]}</p></div></div>
    {error && <div className="form-error" role="alert">{error}</div>}
    {notice && <p className="pilot-success" role="status">{notice}</p>}
    {loading && <p role="status">正在加载…</p>}
    {page === "calendar" && <section className="pilot-card">
      <div className="pilot-toolbar"><label>查看月份<input aria-label="查看月份" type="month" value={month} onChange={e => { if (e.target.value) setMonth(e.target.value); }} /></label><p className="muted">默认周一至周五工作 · 09:00–12:00 / 13:30–18:00<br />节假日和补班以 HR 设置为准，提交后保留当时日历。</p></div>
      <div className="calendar-grid">{["一", "二", "三", "四", "五", "六", "日"].map(d => <span className="weekday" key={d}>周{d}</span>)}{Array.from({ length: offset }, (_, i) => <span key={`blank-${i}`} />)}{Array.from({ length: monthLength }, (_, i) => {
        const key = `${month}-${String(i + 1).padStart(2, "0")}`;
        const override = days.find(d => d.day === key);
        const working = override ? Boolean(override.isWorkday) : (offset + i) % 7 < 5;
        return <button key={key} className={`calendar-day ${working ? "working" : "rest"} ${day === key ? "selected" : ""}`} onClick={() => selectDay(key)} aria-label={`${key} ${working ? "工作日" : "休息日"} ${override?.name ?? ""}`}><strong>{i + 1}</strong><small>{working ? "班" : "休"}</small><span>{override?.name ?? ""}</span></button>;
      })}</div>
      {admin ? <form className="pilot-section pilot-form" onSubmit={e => { e.preventDefault(); void run(async () => { setDays((await pilotApi.saveDay({ day, isWorkday: selectedWorkday, name: selectedName })).days); setWorkday(undefined); setName(undefined); }); }}><h3>节假日与调休设置</h3><fieldset disabled={busy || loading}><div className="pilot-form-grid"><label>日期<input required type="date" value={day} onChange={e => selectDay(e.target.value)} /></label><label>安排<select aria-label="安排" value={selectedWorkday ? "work" : "rest"} onChange={e => setWorkday(e.target.value === "work")}><option value="rest">休息日</option><option value="work">工作日 / 补班</option></select></label><label>名称或说明<input required maxLength={100} value={selectedName} onChange={e => setName(e.target.value)} placeholder="例如：国庆节 / 调休补班" /></label></div><div className="pilot-actions"><button className="button primary">保存日历设置</button>{days.some(d => d.day === day) && <button type="button" className="button ghost" onClick={() => void run(async () => { await pilotApi.resetDay(day); await load(); setWorkday(undefined); setName(undefined); })}>恢复默认安排</button>}</div></fieldset></form> : <p className="muted">选中日期：{day} · {days.find(d => d.day === day)?.name || "默认安排"}。如有误请联系 HR。</p>}
    </section>}
    {page === "ledger" && <>
      <div className="pilot-toolbar">{admin && <label>员工<select aria-label="台账员工" value={employee} disabled={busy} onChange={e => setEmployee(e.target.value)}>{data.users.map(u => <option key={u.id} value={u.id}>{u.name} · {u.department}{u.status === "inactive" ? "（已停用）" : ""}</option>)}</select></label>}<a className="button ghost" href={`${API_BASE}/api/balance-ledger/${employee}/export`}>导出台账 CSV</a></div>
      {ledger && <><div className="pilot-balances">{ledger.balances.map(b => <div className="pilot-card" key={b.leaveType}><h3>{leaveTypeLabels[b.leaveType]}</h3><strong>{b.remainingHours} 小时可用</strong><p className="muted">总额度 {b.totalHours} · 已用 {b.usedHours}</p></div>)}</div>
        {admin && <form className="pilot-card pilot-form" onSubmit={adjust}><h3>调整假期额度</h3><p className="muted">增加输入正数，减少输入负数。调整必须填写依据，不能低于已用额度。</p><fieldset disabled={busy || loading}><div className="pilot-form-grid"><label>假别<select value={type} onChange={e => setType(e.target.value as LeaveType)}>{Object.entries(leaveTypeLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>调整小时数<input required type="number" step="0.01" min={-2000} max={2000} value={amount} onChange={e => setAmount(e.target.value)} /></label><label>调整依据<input required minLength={2} maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label></div><button className="button primary">确认调整额度</button></fieldset></form>}
        <section className="pilot-card"><h3>额度流水</h3><div className="pilot-table-scroll"><table className="pilot-table"><thead><tr><th>时间 / 假别</th><th>总额度变动</th><th>已用变动</th><th>原因 / 操作人</th><th>申请</th></tr></thead><tbody>{ledger.entries.map(entry => <tr key={entry.id}><td>{dateTime(entry.createdAt)}<br />{leaveTypeLabels[entry.leaveType]}</td><td>{entry.totalDelta > 0 ? "+" : ""}{entry.totalDelta}</td><td>{entry.usedDelta > 0 ? "+" : ""}{entry.usedDelta}</td><td>{entry.reason}<br /><small>{entry.actorName}</small></td><td>{entry.requestId ? <button className="text-button" onClick={() => void onOpen(entry.requestId!)}>查看申请</button> : "—"}</td></tr>)}</tbody></table></div>{!ledger.entries.length && <p className="muted">暂无流水</p>}</section></>}
    </>}
    {page === "notifications" && <section className="pilot-card"><div className="pilot-toolbar"><label className="pilot-check"><input type="checkbox" checked={unreadOnly} onChange={e => setUnreadOnly(e.target.checked)} />只看未读</label><button className="button ghost" disabled={busy} onClick={() => void run(async () => { await pilotApi.read(); await load(); })}>全部标为已读</button></div><p className="muted">显示最近 200 条消息</p>{messages.filter(m => !unreadOnly || !m.readAt).map(m => <article className={`notification-item ${m.readAt ? "read" : "unread"}`} key={m.id}><div><strong>{!m.readAt && "● "}{m.title}</strong><p>{m.body}</p><small>{dateTime(m.createdAt)}</small></div><div className="pilot-actions">{m.requestId && <button className="button ghost" disabled={busy} onClick={() => void run(async () => { await pilotApi.read(m.id); await load(); await onOpen(m.requestId!); })}>查看申请</button>}{!m.readAt && <button className="text-button" disabled={busy} onClick={() => void run(async () => { await pilotApi.read(m.id); await load(); })}>标为已读</button>}</div></article>)}{!messages.some(m => !unreadOnly || !m.readAt) && <div className="empty-state">暂无消息</div>}</section>}
    {page === "admin" && admin && <section className="pilot-card"><div className="pilot-toolbar"><label>部门<select value={department} onChange={e => setDepartment(e.target.value)}><option value="">全部部门</option>{departments.map(d => <option key={d}>{d}</option>)}</select></label><label>状态<select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(statusLabels).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></label></div>{requests.filter(r => (!department || r.department === department) && (!status || r.status === status)).map(r => <button className="pilot-notification" key={r.id} onClick={() => onSelect(r)}><strong>{r.applicantName} · {leaveTypeLabels[r.leaveType]} · {statusLabels[r.status]}</strong><p>{r.department} · {r.durationHours} 小时 · {r.reason}</p><span>{dateTime(r.startAt)} 至 {dateTime(r.endAt)} · 审批人：{r.currentApproverName ?? "无待处理节点"}</span></button>)}{!requests.length && <div className="empty-state">暂无申请</div>}</section>}
  </>;
}
