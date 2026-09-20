import { useEffect, useMemo, useState, type FormEvent } from "react";
import { CheckCircle2, Clock3, FileText, LoaderCircle, Plus, Receipt, Send, XCircle } from "lucide-react";
import { api, type BootstrapData, type ExpenseCategory, type ExpensePaymentMethod, type ExpenseRequest, type ExpenseStatus, type TravelRequest } from "./api";

const categoryLabels: Record<ExpenseCategory, string> = { travel: "差旅", meal: "餐饮", office: "办公用品", software: "软件服务", other: "其他" };
const paymentLabels: Record<ExpensePaymentMethod, string> = { personal: "个人垫付", corporate_card: "公司卡", cash: "现金", other: "其他" };
const statusLabels: Record<ExpenseStatus, string> = { draft: "草稿", human_reviewing: "审批中", approved: "已通过", rejected: "已驳回", withdrawn: "已撤回" };

type Tab = "mine" | "inbox" | "history";

export default function ExpensePage({ userId, data, onChanged }: { userId: string; data: BootstrapData; onChanged: () => Promise<void> }) {
  const [lists, setLists] = useState(data.expenses);
  const [tab, setTab] = useState<Tab>(data.currentUser.permissions.approveLeave ? "inbox" : "mine");
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setLoading(true); setError("");
    try { setLists(await api.expenses(userId)); } catch (cause) { setError(cause instanceof Error ? cause.message : "报销数据加载失败"); } finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [userId]);

  async function run(operation: () => Promise<void>, success = "操作已完成") {
    setError(""); setNotice("");
    try { await operation(); setNotice(success); await load(); await onChanged(); } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); }
  }

  const items = lists[tab];
  return <>
    <div className="page-heading expense-heading"><div><span className="eyebrow">EXPENSE CLAIMS</span><h1>费用报销</h1><p>差旅、餐饮和办公支出在线提交，审批过程全程留痕。</p></div><button className="button primary" onClick={() => setShowForm(true)}><Plus size={17} />新建报销</button></div>
    {error && <div className="form-error" role="alert"><XCircle size={16} />{error}</div>}{notice && <p className="pilot-success" role="status">{notice}</p>}
    <div className="expense-tabs" role="tablist">{(["mine", "inbox", "history"] as const).map(key => <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{key === "mine" ? "我的报销" : key === "inbox" ? "待我审批" : "已处理"}<span>{lists[key].length}</span></button>)}</div>
    {loading && <p className="muted"><LoaderCircle className="spin" size={16} /> 正在加载…</p>}
    <section className="expense-list">{items.map(item => <ExpenseCard key={item.id} item={item} canDecide={tab === "inbox"} canSubmit={tab === "mine"} userId={userId} onDone={(message) => void run(async () => { if (message === "submit") await api.submitExpense(userId, item.id, item.version); else if (message === "withdraw") await api.withdrawExpense(userId, item.id, "申请人撤回"); else { const reason = window.prompt(message === "approve" ? "审批意见（可选）" : "驳回原因（必填）", message === "approve" ? "同意报销" : "请补充合规票据和用途说明") ?? ""; await api.decideExpense(userId, item.id, message, reason, item.version); } }, message === "submit" ? "报销已提交" : "审批操作已完成")} />)}
      {!items.length && <div className="empty-state"><Receipt size={32} /><strong>{tab === "inbox" ? "暂无待审批报销" : "暂无报销记录"}</strong></div>}</section>
    {showForm && <ExpenseForm userId={userId} trips={data.travelRequests.mine.filter(item => item.status === "approved")} onClose={() => setShowForm(false)} onCreated={() => { setShowForm(false); void run(async () => undefined, "报销草稿已保存"); }} />}
  </>;
}

function ExpenseCard({ item, canDecide, canSubmit, userId, onDone }: { item: ExpenseRequest; canDecide: boolean; canSubmit: boolean; userId: string; onDone: (action: "submit" | "withdraw" | "approve" | "reject") => void }) {
  return <article className="expense-card"><div className="expense-card-main"><span className={`expense-icon ${item.status}`}><Receipt size={18} /></span><div><div className="expense-title"><strong>{categoryLabels[item.category]}报销 · {item.amount.toFixed(2)} {item.currency}</strong><span className={`status-badge ${item.status === "approved" ? "success" : item.status === "rejected" || item.status === "withdrawn" ? "danger" : item.status === "draft" ? "neutral" : "warning"}`}>{statusLabels[item.status]}</span></div><p>{item.description}{item.travelDestination ? ` · 关联出差：${item.travelDestination}` : ""}</p><small>{item.applicantName} · {item.department} · {item.occurredAt} · {paymentLabels[item.paymentMethod]}</small></div></div><div className="expense-actions">{canSubmit && item.status === "draft" && <><button className="button primary" onClick={() => onDone("submit")}><Send size={14} />提交审批</button><button className="button ghost" onClick={() => onDone("withdraw")}>删除草稿</button></>}{canDecide && item.status === "human_reviewing" && <><button className="button primary" onClick={() => onDone("approve")}><CheckCircle2 size={14} />批准</button><button className="button danger-outline" onClick={() => onDone("reject")}><XCircle size={14} />驳回</button></>}</div></article>;
}

function ExpenseForm({ userId, trips, onClose, onCreated }: { userId: string; trips: TravelRequest[]; onClose: () => void; onCreated: () => void }) {
  const [category, setCategory] = useState<ExpenseCategory>("travel");
  const [amount, setAmount] = useState("");
  const [occurredAt, setOccurredAt] = useState(new Date().toISOString().slice(0, 10));
  const [description, setDescription] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<ExpensePaymentMethod>("personal");
  const [travelRequestId, setTravelRequestId] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await api.createExpense(userId, { category, amount: Number(amount), currency: "CNY", occurredAt, description, paymentMethod, travelRequestId: travelRequestId || null }); onCreated(); } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败"); } finally { setBusy(false); } }
  return <div className="expense-backdrop"><form className="expense-dialog" onSubmit={submit}><header><div><span className="eyebrow">NEW CLAIM</span><h2>新建费用报销</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><XCircle size={18} /></button></header><div className="expense-form-grid"><label>费用类型<select value={category} onChange={e => setCategory(e.target.value as ExpenseCategory)}>{Object.entries(categoryLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>金额（元）<input required type="number" min="0.01" max="10000000" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} /></label><label>发生日期<input required type="date" value={occurredAt} onChange={e => setOccurredAt(e.target.value)} /></label><label>支付方式<select value={paymentMethod} onChange={e => setPaymentMethod(e.target.value as ExpensePaymentMethod)}>{Object.entries(paymentLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="full">关联出差申请<select value={travelRequestId} onChange={e => setTravelRequestId(e.target.value)}><option value="">不关联</option>{trips.map(trip => <option key={trip.id} value={trip.id}>{trip.destination} · {trip.startAt.slice(0, 10)} 至 {trip.endAt.slice(0, 10)}</option>)}</select></label><label className="full">用途说明<textarea required minLength={2} maxLength={2000} rows={4} value={description} onChange={e => setDescription(e.target.value)} placeholder="请说明费用用途、项目或客户" /></label></div>{error && <div className="form-error"><XCircle size={15} />{error}</div>}<footer><span><Clock3 size={14} />保存后可在列表中提交审批</span><button type="button" className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <FileText size={15} />}保存草稿</button></footer></form></div>;
}
