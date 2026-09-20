import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Bot,
  ChartNoAxesCombined,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileText,
  Inbox,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquareText,
  Plus,
  Plane,
  ShoppingCart,
  ShieldCheck,
  Sparkles,
  Timer,
  Users,
  XCircle,
} from "lucide-react";
import {
  api,
  leaveTypeLabels,
  statusLabels,
  type ApprovalHistoryItem,
  type BootstrapData,
  type LeaveRequest,
  type LeaveStatus,
  type LeaveType,
} from "./api";
import OrganizationPage, { OrganizationSummary } from "./OrganizationPage";
import AuthGate from "./Auth";
import LeaveDrawer from "./LeaveDetail";
import PilotPage from "./PilotPage";
import Assistant from "./Assistant";
import AnalyticsPage from "./AnalyticsPage";
import ExpensePage from "./ExpensePage";
import TravelPage from "./TravelPage";
import ProcurementPage from "./ProcurementPage";
import OvertimePage from "./OvertimePage";

type Page = "assistant" | "dashboard" | "apply" | "mine" | "inbox" | "organization" | "calendar" | "ledger" | "notifications" | "admin" | "analytics" | "expenses" | "travel" | "procurement" | "overtime";

const navItems: { page: Page; label: string; icon: typeof LayoutDashboard }[] = [
  { page: "dashboard", label: "工作台", icon: LayoutDashboard },
  { page: "assistant", label: "智能助手", icon: Bot },
  { page: "apply", label: "发起请假", icon: Plus },
  { page: "mine", label: "我的申请", icon: FileText },
  { page: "inbox", label: "我的审批", icon: Inbox },
  { page: "calendar", label: "工作日历", icon: CalendarDays },
  { page: "ledger", label: "假期台账", icon: FileText },
  { page: "notifications", label: "消息通知", icon: MessageSquareText },
  { page: "expenses", label: "费用报销", icon: FileText },
  { page: "travel", label: "出差申请", icon: Plane },
  { page: "procurement", label: "采购申请", icon: ShoppingCart },
  { page: "overtime", label: "加班与调休", icon: Timer },
];

function formatDateTime(value: string, includeYear = false): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: includeYear ? "numeric" : undefined,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function statusTone(status: LeaveStatus): string {
  if (status === "approved") return "success";
  if (status === "rejected" || status === "cancelled") return "danger";
  if (status === "draft") return "neutral";
  return "warning";
}

function StatusBadge({ status }: { status: LeaveStatus }) {
  return <span className={`status-badge ${statusTone(status)}`}>{statusLabels[status]}</span>;
}

function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  return <div className={`avatar ${small ? "avatar-small" : ""}`}>{name.slice(-2)}</div>;
}

function RequestTable({ items, onSelect, emptyLabel = "这里暂时没有内容" }: {
  items: (LeaveRequest | ApprovalHistoryItem)[];
  onSelect: (item: LeaveRequest) => void;
  emptyLabel?: string;
}) {
  if (items.length === 0) {
    return (
      <div className="empty-state">
        <CheckCircle2 size={32} />
        <strong>{emptyLabel}</strong>
      </div>
    );
  }
  return (
    <div className="request-list">
      {items.map((item) => (
        <button className={`request-row ${"reviewedAt" in item ? "history-row" : ""}`} key={item.id} onClick={() => onSelect(item)}>
          <div className="request-person">
            <Avatar name={item.applicantName} small />
            <div>
              <strong>{item.applicantName}的{leaveTypeLabels[item.leaveType]}申请</strong>
              <span>{item.department} · {item.reason}</span>
              {"reviewedAt" in item && <>
                <span className={`review-summary ${item.reviewDecision}`}>我已{({ approved: "批准请假", rejected: "驳回请假", returned: "退回补充", transferred: "转交审批", cancellation_approved: "批准销假", cancellation_rejected: "驳回销假" })[item.reviewDecision]} · {formatDateTime(item.reviewedAt, true)}</span>
                <span title={item.reviewComment}>审批意见：{item.reviewComment}</span>
              </>}
            </div>
          </div>
          <div className="request-time">
            <strong>{formatDateTime(item.startAt, "reviewedAt" in item)} 至 {formatDateTime(item.endAt, "reviewedAt" in item)}</strong>
            <span>共 {item.durationHours} 小时</span>
          </div>
          <StatusBadge status={item.status} />
          <ChevronRight size={18} />
        </button>
      ))}
    </div>
  );
}

function ManualLeaveForm({ userId, onCreated }: { userId: string; onCreated: (leave: LeaveRequest) => void }) {
  const tomorrow = useMemo(() => {
    const date = new Date(Date.now() + 86_400_000);
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, "0");
    const dd = String(date.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }, []);
  const [leaveType, setLeaveType] = useState<LeaveType>("personal");
  const [startAt, setStartAt] = useState(`${tomorrow}T13:30`);
  const [endAt, setEndAt] = useState(`${tomorrow}T18:00`);
  const [reason, setReason] = useState("");
  const [handoverUser, setHandoverUser] = useState("");
  const [handoverNotes, setHandoverNotes] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.createLeave(userId, { leaveType, startAt: startAt + ":00+08:00", endAt: endAt + ":00+08:00", reason, handoverUser, handoverNotes });
      onCreated(result.leave);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="form-card" onSubmit={submit}>
      <div className="form-intro">
        <span className="eyebrow">MANUAL REQUEST</span>
        <h2>手动填写请假申请</h2>
        <p>你也可以完整填写表单，保存后可上传材料，确认时长后提交人工审批。</p>
      </div>
      <div className="form-grid">
        <label>
          <span>请假类型</span>
          <select value={leaveType} onChange={(event) => setLeaveType(event.target.value as LeaveType)}>
            <option value="annual">年假</option>
            <option value="personal">事假</option>
            <option value="sick">病假</option>
          </select>
        </label>
        <div />
        <label><span>开始时间</span><input required type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label>
        <label><span>结束时间</span><input required type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label>
        <label className="full"><span>请假原因</span><textarea required minLength={2} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></label>
        <label><span>工作交接人</span><input value={handoverUser} onChange={(event) => setHandoverUser(event.target.value)} /></label>
        <label><span>交接说明</span><input value={handoverNotes} onChange={(event) => setHandoverNotes(event.target.value)} /></label>
      </div>
      {error && <div className="form-error"><XCircle size={16} />{error}</div>}
      <div className="form-footer">
        <span><ShieldCheck size={15} />保存为草稿后，需要再次确认提交</span>
        <button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <FileText size={16} />}创建草稿</button>
      </div>
    </form>
  );
}

export default function App() {
  return <AuthGate>{(user, logout, password) => <Workspace key={user.id} userId={user.id} onLogout={logout} onPassword={password} />}</AuthGate>;
}

function Workspace({ userId, onLogout, onPassword }: { userId: string; onLogout: () => void; onPassword: () => void }) {
  const [page, setPage] = useState<Page>("dashboard");
  const [approvalView, setApprovalView] = useState<"pending" | "history">("pending");
  const [data, setData] = useState<BootstrapData | null>(null);
  const [selected, setSelected] = useState<LeaveRequest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const refreshSequence = useRef(0);
  const [search, setSearch] = useState("");
  const [assistantContext, setAssistantContext] = useState<string>();

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    try {
      const result = await api.bootstrap(userId);
      if (sequence !== refreshSequence.current) return;
      setData(result);
      setError("");
    } catch (cause) {
      if (sequence !== refreshSequence.current) return;
      setError(cause instanceof Error ? cause.message : "无法连接后端服务");
    } finally {
      if (sequence === refreshSequence.current) setLoading(false);
    }
  }, [userId]);

  useEffect(() => { setLoading(true); void refresh(); }, [refresh]);

  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function openRequest(id: string) {
    try { setSelected((await api.details(userId, id)).leave); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "无法打开申请"); }
  }

  if (loading && !data) {
    return <div className="loading-screen"><LoaderCircle className="spin" /><span>正在进入 FlowMind OA…</span></div>;
  }

  if (!data) {
    return <div className="loading-screen error-screen"><XCircle /><span>{error || "加载失败"}</span><button onClick={() => void refresh()}>重新连接</button></div>;
  }

  const recent = [...new Map([...data.mine, ...data.inbox, ...data.approvalHistory].map((item) => [item.id, item])).values()]
    .filter((item) => item.status !== "draft")
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, 4);

  function navigate(next: Page) {
    setPage(next);
    setMobileNav(false);
  }

  async function submitCreated(leave: LeaveRequest) {
    setSelected(leave);
    await refresh();
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="brand"><div className="brand-mark"><Sparkles size={21} /></div><div><strong>FlowMind</strong><span>智能协同办公</span></div></div>
        <nav>
          <span className="nav-caption">办公中心</span>
          {navItems.filter(item => item.page !== "inbox" || data.currentUser.permissions.approveLeave).map(({ page: itemPage, label, icon: Icon }) => (
            <button className={page === itemPage ? "active" : ""} key={itemPage} onClick={() => navigate(itemPage)}>
              <Icon size={19} />{label}
              {itemPage === "inbox" && data.stats.pending > 0 && <span className="nav-count">{data.stats.pending}</span>}
              {itemPage === "expenses" && data.stats.expensePending > 0 && <span className="nav-count">{data.stats.expensePending}</span>}
              {itemPage === "travel" && data.stats.travelPending > 0 && <span className="nav-count">{data.stats.travelPending}</span>}
              {itemPage === "procurement" && data.stats.procurementPending > 0 && <span className="nav-count">{data.stats.procurementPending}</span>}
              {itemPage === "overtime" && data.stats.overtimePending > 0 && <span className="nav-count">{data.stats.overtimePending}</span>}
              {itemPage === "notifications" && data.unreadNotifications > 0 && <span className="nav-count">{data.unreadNotifications}</span>}
            </button>
          ))}
          <span className="nav-caption second">组织协同</span>
          <button className={page === "organization" ? "active" : ""} onClick={() => navigate("organization")}><Users size={19} />{data.currentUser.permissions.manageOrganization ? "组织与人员" : "通讯录"}</button>
          {data.currentUser.permissions.manageOrganization && <button className={page === "admin" ? "active" : ""} onClick={() => navigate("admin")}><Inbox size={19} />请假管理</button>}
          {data.currentUser.permissions.manageOrganization && <button className={page === "analytics" ? "active" : ""} onClick={() => navigate("analytics")}><ChartNoAxesCombined size={19} />数据分析</button>}
        </nav>
        <div className="sidebar-footer">
          <div className="security-chip"><ShieldCheck size={16} /><div><strong>安全运行中</strong><span>操作全程留痕</span></div></div>
          <button onClick={onLogout}><LogOut size={18} />退出登录</button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <button className="mobile-menu" aria-label="打开导航" onClick={() => setMobileNav((value) => !value)}><Menu size={21} /></button>
          <span className="workspace-caption">FlowMind · 智能 OA 审批中心</span>
          <div className="top-actions">
            <span className="agent-mode"><Sparkles size={14} />{data.agentMode === "deep-agent" ? "AI 辅助 · 人工审批" : "智能体未配置 · 人工审批"}</span>
            <button className="icon-button" aria-label="打开智能助手" title="打开智能助手" onClick={() => navigate("assistant")}><Bot size={18} /></button>
            <span className="signed-in-user" title={`${data.currentUser.name} · ${data.currentUser.title}`}>{data.currentUser.name} · {data.currentUser.title}</span>
            <button className="icon-button" title="修改密码" aria-label="修改密码" onClick={onPassword}><KeyRound size={18} /></button>
            <Avatar name={data.currentUser.name} small />
          </div>
        </header>

        <div className="content">
          {error && <div className="toast-error"><XCircle size={16} />{error}</div>}

          {page === "dashboard" && (
            <>
              <div className="page-heading">
                <div><span className="eyebrow">GOOD AFTERNOON</span><h1>{data.currentUser.name}，今天想处理什么？</h1><p>你的审批、出差、报销、采购、加班调休、假期余额与智能助手都在这里。</p></div>
                <button className="button primary" onClick={() => setPage("apply")}><Plus size={17} />发起申请</button>
              </div>
              <OrganizationSummary data={data} />
              <div className="metrics-grid">
                <div className="metric-card"><span className="metric-icon amber"><Clock3 /></span><div><span>{data.currentUser.permissions.approveLeave ? "待我审批" : "待提交"}</span><strong>{data.currentUser.permissions.approveLeave ? data.stats.pending : data.mine.filter(item => item.status === "draft" || item.status === "need_information").length}</strong><small>{data.currentUser.permissions.approveLeave ? "需要及时处理" : "我的草稿与待补充申请"}</small></div></div>
                <div className="metric-card"><span className="metric-icon blue"><FileText /></span><div><span>审批中</span><strong>{data.stats.inProgress}</strong><small>我的申请</small></div></div>
                <div className="metric-card"><span className="metric-icon green"><CheckCircle2 /></span><div><span>已通过</span><strong>{data.stats.approved}</strong><small>流程顺利完成</small></div></div>
                <div className="metric-card"><span className="metric-icon violet"><Bot /></span><div><span>辅助检查</span><strong>{data.stats.agentHandled}</strong><small>等待人工最终确认</small></div></div>
              </div>
              <div className="dashboard-grid">
                <section className="assistant-launch"><Sparkles size={28} /><h2>智能 OA 助手</h2><p>请假、出差、采购、加班调休、销假、材料和进度查询{data.currentUser.permissions.approveLeave ? "，以及审批待办" : ""}{data.currentUser.permissions.manageOrganization ? "和人员管理" : ""}，直接说出需求，在对话中核对并完成。</p><button className="button primary" onClick={() => navigate("assistant")}>打开智能助手</button></section>
                <section className="balance-card">
                  <div className="section-heading"><div><span className="eyebrow">BALANCE</span><h2>我的假期</h2></div><CalendarDays size={20} /></div>
                  <div className="balance-list">
                    {data.balances.map((balance) => {
                      const percent = balance.totalHours > 0 ? Math.max(0, Math.min(100, (balance.remainingHours / balance.totalHours) * 100)) : 0;
                      return <div key={balance.leaveType} className="balance-item">
                        <div><strong>{leaveTypeLabels[balance.leaveType]}</strong><span><b>{balance.remainingHours}</b> / {balance.totalHours} 小时</span></div>
                        <div className="progress"><span style={{ width: `${percent}%` }} /></div>
                      </div>;
                    })}
                  </div>
                  <div className="policy-tip"><ShieldCheck size={18} /><div><strong>请假试点规则</strong><p>按工作日历计算时长，批准后扣减额度，销假批准后返还。所有申请均由人工审批。</p></div></div>
                </section>
              </div>
              <section className="list-card">
                <div className="section-heading"><div><span className="eyebrow">RECENT</span><h2>最近流程</h2></div><button onClick={() => setPage("mine")}>查看全部 <ChevronRight size={15} /></button></div>
                <RequestTable items={recent} onSelect={setSelected} />
              </section>
            </>
          )}

          {page === "assistant" && <><div className="page-heading"><div><h1>对话办事</h1><p>说出需求，核对预览，确认后办理。</p></div></div><Assistant user={data.currentUser} contextId={assistantContext} onContextUsed={() => setAssistantContext(undefined)} onChanged={refresh} /></>}

          {page === "apply" && (
            <>
              <div className="page-heading"><div><span className="eyebrow">NEW REQUEST</span><h1>发起请假</h1><p>可以手动填写，也可以使用左侧智能助手在对话中办理。</p></div></div>
              <OrganizationSummary data={data} />
              <ManualLeaveForm userId={userId} onCreated={submitCreated} />
            </>
          )}

          {page === "mine" && (
            <>
              <div className="page-heading"><div><span className="eyebrow">MY REQUESTS</span><h1>我的申请</h1><p>查看自己发起的全部请假流程。</p></div><button className="button primary" onClick={() => setPage("apply")}><Plus size={17} />发起申请</button></div>
              <label className="pilot-search">查找申请<input value={search} onChange={e => setSearch(e.target.value)} placeholder="输入原因、假别或状态" /></label>
              <section className="list-card"><RequestTable items={data.mine.filter(i => `${i.reason} ${leaveTypeLabels[i.leaveType]} ${statusLabels[i.status]}`.includes(search))} onSelect={setSelected} /></section>
            </>
          )}

          {page === "inbox" && data.currentUser.permissions.approveLeave && (
            <>
              <div className="page-heading"><div><span className="eyebrow">MY APPROVALS</span><h1>我的审批</h1></div></div>
              <div className="approval-tabs" role="tablist" aria-label="审批列表">
                {(["pending", "history"] as const).map((view) => (
                  <button key={view} id={`approval-tab-${view}`} role="tab" aria-selected={approvalView === view} aria-controls="approval-panel" tabIndex={approvalView === view ? 0 : -1} onClick={() => setApprovalView(view)} onKeyDown={(event) => {
                    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                    event.preventDefault();
                    const next = event.key === "Home" ? "pending" : event.key === "End" ? "history" : view === "pending" ? "history" : "pending";
                    setApprovalView(next);
                    document.getElementById(`approval-tab-${next}`)?.focus();
                  }}>
                    {view === "pending" ? "待我审批" : "已处理"}<span>{view === "pending" ? data.stats.pending : data.approvalHistory.length}</span>
                  </button>
                ))}
              </div>
              <section className="approval-panel" id="approval-panel" role="tabpanel" aria-labelledby={`approval-tab-${approvalView}`} tabIndex={0}>
                {approvalView === "pending" && data.cancellationInbox.map(c => <button className="pilot-notification" key={c.id} onClick={() => void openRequest(c.requestId)}><strong>{c.applicantName}的销假申请 · {c.hours} 小时</strong><p>{c.reason}</p><span>销假待审批 · {formatDateTime(c.startAt)} 至 {formatDateTime(c.endAt)}</span></button>)}
                <RequestTable items={approvalView === "pending" ? data.inbox : data.approvalHistory} onSelect={setSelected} emptyLabel={approvalView === "pending" ? "暂无待审批申请" : "暂无已处理的审批记录"} />
              </section>
            </>
          )}
          {(["calendar", "ledger", "notifications", "admin"] as string[]).includes(page) && <PilotPage key={page} page={page as "calendar" | "ledger" | "notifications" | "admin"} data={data} onChanged={refresh} onSelect={setSelected} onOpen={openRequest} />}
          {page === "analytics" && data.currentUser.permissions.manageOrganization && <AnalyticsPage userId={userId} data={data} onOpen={openRequest} />}
          {page === "expenses" && <ExpensePage userId={userId} data={data} onChanged={refresh} />}
          {page === "travel" && <TravelPage userId={userId} data={data} onChanged={refresh} />}
          {page === "procurement" && <ProcurementPage userId={userId} data={data} onChanged={refresh} />}
          {page === "overtime" && <OvertimePage userId={userId} data={data} onChanged={refresh} />}
          {page === "organization" && <OrganizationPage key={userId} data={data} onChanged={refresh} />}
        </div>
      </main>

      {selected && <LeaveDrawer key={`${userId}:${selected.id}`} item={selected} user={data.currentUser} users={data.users} onClose={() => setSelected(null)} onChanged={refresh} onAssistant={() => { setAssistantContext(selected.id); setSelected(null); navigate("assistant"); }} />}
    </div>
  );
}
