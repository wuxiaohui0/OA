import { useEffect, useState, type FormEvent } from "react";
import { CalendarRange, CheckCircle2, Clock3, FileText, LoaderCircle, MapPin, Plane, Send, UsersRound, XCircle } from "lucide-react";
import { api, type BootstrapData, type TravelAccommodationStandard, type TravelRequest, type TravelStatus, type TravelTransportStandard } from "./api";

const statusLabels: Record<TravelStatus, string> = { draft: "草稿", human_reviewing: "审批中", approved: "已通过", rejected: "已驳回", withdrawn: "已撤回" };
const transportLabels: Record<TravelTransportStandard, string> = { economy: "经济舱/普通列车", high_speed: "高铁二等座", business: "商务舱/高铁一等座" };
const accommodationLabels: Record<TravelAccommodationStandard, string> = { none: "不住宿", standard: "标准酒店", premium: "高标准酒店" };
type Tab = "mine" | "inbox" | "history";

export default function TravelPage({ userId, data, onChanged }: { userId: string; data: BootstrapData; onChanged: () => Promise<void> }) {
  const [lists, setLists] = useState(data.travelRequests);
  const [tab, setTab] = useState<Tab>(data.currentUser.permissions.approveLeave ? "inbox" : "mine");
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function load() { setLoading(true); setError(""); try { setLists(await api.travelRequests(userId)); } catch (cause) { setError(cause instanceof Error ? cause.message : "出差数据加载失败"); } finally { setLoading(false); } }
  useEffect(() => { void load(); }, [userId]);
  async function run(operation: () => Promise<void>, success = "操作已完成") { setError(""); setNotice(""); try { await operation(); setNotice(success); await load(); await onChanged(); } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); } }
  const items = lists[tab];
  return <>
    <div className="page-heading expense-heading"><div><span className="eyebrow">BUSINESS TRIPS</span><h1>出差申请</h1><p>出差计划先审批，审批通过后可在费用报销中提交差旅费用。</p></div><button className="button primary" onClick={() => setShowForm(true)}><Plane size={17} />新建出差</button></div>
    {error && <div className="form-error" role="alert"><XCircle size={16} />{error}</div>}{notice && <p className="pilot-success" role="status">{notice}</p>}
    <div className="expense-tabs" role="tablist">{(["mine", "inbox", "history"] as const).map(key => <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{key === "mine" ? "我的出差" : key === "inbox" ? "待我审批" : "已处理"}<span>{lists[key].length}</span></button>)}</div>
    {loading && <p className="muted"><LoaderCircle className="spin" size={16} /> 正在加载…</p>}
    <section className="expense-list">{items.map(item => <TravelCard key={item.id} item={item} canDecide={tab === "inbox"} canSubmit={tab === "mine"} onDone={(action) => void run(async () => { if (action === "submit") await api.submitTravel(userId, item.id, item.version); else if (action === "withdraw") await api.withdrawTravel(userId, item.id, "申请人撤回"); else { const reason = window.prompt(action === "approve" ? "审批意见（可选）" : "驳回原因（必填）", action === "approve" ? "同意出差" : "请补充行程和预算说明") ?? ""; await api.decideTravel(userId, item.id, action, reason, item.version); } }, action === "submit" ? "出差申请已提交" : "审批操作已完成")} />)}{!items.length && <div className="empty-state"><Plane size={32} /><strong>{tab === "inbox" ? "暂无待审批出差" : "暂无出差记录"}</strong></div>}</section>
    {showForm && <TravelForm userId={userId} users={data.users} onClose={() => setShowForm(false)} onCreated={() => { setShowForm(false); void run(async () => undefined, "出差草稿已保存"); }} />}
  </>;
}

function TravelCard({ item, canDecide, canSubmit, onDone }: { item: TravelRequest; canDecide: boolean; canSubmit: boolean; onDone: (action: "submit" | "withdraw" | "approve" | "reject") => void }) {
  const tone = item.status === "approved" ? "success" : item.status === "rejected" || item.status === "withdrawn" ? "danger" : item.status === "draft" ? "neutral" : "warning";
  return <article className="expense-card"><div className="expense-card-main"><span className={`expense-icon ${item.status}`}><MapPin size={18} /></span><div><div className="expense-title"><strong>{item.destination} · {item.purpose}</strong><span className={`status-badge ${tone}`}>{statusLabels[item.status]}</span></div><p>{item.startAt.slice(0, 10)} 至 {item.endAt.slice(0, 10)} · {transportLabels[item.transportStandard]} · {accommodationLabels[item.accommodationStandard]}</p><small>{item.applicantName} · {item.department} · 同行：{item.travelerNames.join("、")}</small></div></div><div className="expense-actions">{canSubmit && item.status === "draft" && <><button className="button primary" onClick={() => onDone("submit")}><Send size={14} />提交审批</button><button className="button ghost" onClick={() => onDone("withdraw")}>删除草稿</button></>}{canDecide && item.status === "human_reviewing" && <><button className="button primary" onClick={() => onDone("approve")}><CheckCircle2 size={14} />批准</button><button className="button danger-outline" onClick={() => onDone("reject")}><XCircle size={14} />驳回</button></>}</div></article>;
}

function TravelForm({ userId, users, onClose, onCreated }: { userId: string; users: BootstrapData["users"]; onClose: () => void; onCreated: () => void }) {
  const current = users.find(user => user.id === userId);
  const [destination, setDestination] = useState(""); const [purpose, setPurpose] = useState("");
  const [startAt, setStartAt] = useState(""); const [endAt, setEndAt] = useState("");
  const [travelerIds, setTravelerIds] = useState<string[]>([userId]);
  const [transportStandard, setTransportStandard] = useState<TravelTransportStandard>("economy");
  const [accommodationStandard, setAccommodationStandard] = useState<TravelAccommodationStandard>("standard");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await api.createTravel(userId, { destination, purpose, startAt: startAt + ":00+08:00", endAt: endAt + ":00+08:00", travelerIds, transportStandard, accommodationStandard }); onCreated(); } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); } finally { setBusy(false); } }
  function toggleTraveler(id: string) { setTravelerIds(value => value.includes(id) ? value.filter(item => item !== id) : [...value, id]); }
  return <div className="expense-backdrop"><form className="expense-dialog" onSubmit={submit}><header><div><span className="eyebrow">NEW TRIP</span><h2>新建出差申请</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><XCircle size={18} /></button></header><div className="expense-form-grid"><label>出差地点<input required minLength={2} maxLength={200} value={destination} onChange={e => setDestination(e.target.value)} placeholder="城市、客户现场或园区" /></label><label>交通标准<select value={transportStandard} onChange={e => setTransportStandard(e.target.value as TravelTransportStandard)}>{Object.entries(transportLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>出发时间<input required type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} /></label><label>返程时间<input required type="datetime-local" value={endAt} onChange={e => setEndAt(e.target.value)} /></label><label>住宿标准<select value={accommodationStandard} onChange={e => setAccommodationStandard(e.target.value as TravelAccommodationStandard)}>{Object.entries(accommodationLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="full">出差事由<textarea required minLength={2} maxLength={2000} rows={3} value={purpose} onChange={e => setPurpose(e.target.value)} placeholder="说明客户、项目或会议安排" /></label><fieldset className="travel-travelers full"><legend><UsersRound size={14} />同行人</legend><div>{users.filter(user => user.status === "active").map(user => <label key={user.id}><input type="checkbox" checked={travelerIds.includes(user.id)} onChange={() => toggleTraveler(user.id)} />{user.name}{user.id === current?.id ? "（本人）" : ""}</label>)}</div></fieldset></div>{error && <div className="form-error"><XCircle size={15} />{error}</div>}<footer><span><Clock3 size={14} />保存后可在列表中提交审批</span><button type="button" className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <FileText size={15} />}保存草稿</button></footer></form></div>;
}
