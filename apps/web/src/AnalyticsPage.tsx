import { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, Bot, CheckCircle2, Clock3, Download, FileText, LoaderCircle, Users, XCircle } from "lucide-react";
import { API_BASE, api, type ApprovalAnalytics, type AnalyticsBucket, type AnalyticsRisk, type BootstrapData, type LeaveRequest } from "./api";
import { leaveTypeLabels, statusLabels } from "./api";

export default function AnalyticsPage({ userId, data, onOpen }: {
  userId: string;
  data: BootstrapData;
  onOpen: (id: string) => Promise<void>;
}) {
  const [report, setReport] = useState<ApprovalAnalytics>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.approvalAnalytics(userId).then(value => {
      if (active) { setReport(value); setError(""); }
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "分析数据加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [userId]);

  if (loading && !report) return <div className="analytics-loading"><LoaderCircle className="spin" />正在生成审批分析…</div>;
  if (error || !report) return <div className="analytics-error"><XCircle size={18} />{error || "暂无分析数据"}</div>;

  const { summary } = report;
  const maxTrend = Math.max(1, ...report.trend.map(item => item.submitted));
  const maxDepartment = Math.max(1, ...report.byDepartment.map(item => item.total));
  const riskItems = [
    ...report.risks.overdue,
    ...report.risks.missingApprover,
    ...report.risks.aiEscalated,
  ];

  return <>
    <div className="page-heading analytics-heading">
      <div><span className="eyebrow">APPROVAL INSIGHTS</span><h1>审批数据分析</h1><p>基于审批留痕生成运营指标、趋势和需要跟进的风险。</p></div>
      <a className="button ghost" href={`${API_BASE}/api/analytics/approval/export`}><Download size={16} />导出明细 CSV</a>
    </div>
    <div className="analytics-meta">统计范围：全组织 · 更新时间：{new Date(report.generatedAt).toLocaleString("zh-CN", { hour12: false })} · 超时阈值：48 小时</div>
    <div className="analytics-metrics">
      <Metric icon={<FileText />} label="申请总数" value={summary.total} hint={`${summary.totalHours} 小时申请时长`} tone="blue" />
      <Metric icon={<Clock3 />} label="待处理" value={summary.pending} hint={summary.overdue ? `${summary.overdue} 条超过 48 小时` : "暂无超时待办"} tone={summary.overdue ? "amber" : "green"} />
      <Metric icon={<CheckCircle2 />} label="审批通过率" value={summary.approvalRate === null ? "—" : `${summary.approvalRate}%`} hint={`${summary.approved} 通过 · ${summary.rejected} 驳回`} tone="green" />
      <Metric icon={<Bot />} label="平均处理时长" value={summary.avgProcessingHours === null ? "—" : `${summary.avgProcessingHours}h`} hint={`${summary.aiReviewed} 条经过智能检查`} tone="violet" />
    </div>

    <div className="analytics-grid">
      <section className="pilot-card analytics-card"><div className="section-heading"><div><span className="eyebrow">SIX MONTHS</span><h2>申请趋势</h2></div><BarChart3 size={20} /></div>
        <div className="trend-chart">{report.trend.map(item => <div className="trend-column" key={item.period}>
          <div className="trend-bars" title={`${item.period}：提交 ${item.submitted}，通过 ${item.approved}，驳回 ${item.rejected}`}>
            <span className="trend-bar submitted" style={{ height: `${Math.max(4, item.submitted / maxTrend * 100)}%` }} />
            <span className="trend-bar approved" style={{ height: `${Math.max(4, item.approved / maxTrend * 100)}%` }} />
            <span className="trend-bar rejected" style={{ height: `${Math.max(4, item.rejected / maxTrend * 100)}%` }} />
          </div><small>{item.period.slice(5)}月</small><b>{item.submitted}</b>
        </div>)}</div>
        <div className="chart-legend"><span><i className="submitted" />提交</span><span><i className="approved" />通过</span><span><i className="rejected" />驳回</span></div>
      </section>
      <section className="pilot-card analytics-card"><div className="section-heading"><div><span className="eyebrow">DISTRIBUTION</span><h2>假别分布</h2></div><FileText size={20} /></div>
        <BucketBars items={report.byLeaveType} max={Math.max(1, ...report.byLeaveType.map(item => item.total))} />
      </section>
    </div>

    <div className="analytics-grid">
      <section className="pilot-card analytics-card"><div className="section-heading"><div><span className="eyebrow">BY DEPARTMENT</span><h2>部门处理情况</h2></div><Users size={20} /></div>
        <div className="analytics-table-wrap"><table className="pilot-table analytics-table"><thead><tr><th>部门</th><th>申请数</th><th>待处理</th><th>通过率</th><th>平均处理</th></tr></thead><tbody>{report.byDepartment.map(item => <tr key={item.key}><td><strong>{item.label}</strong><div className="mini-bar"><i style={{ width: `${item.total / maxDepartment * 100}%` }} /></div></td><td>{item.total}</td><td>{item.pending}</td><td>{decisionRate(item)}%</td><td>{item.avgProcessingHours === null ? "—" : `${item.avgProcessingHours}h`}</td></tr>)}</tbody></table></div>
        {!report.byDepartment.length && <p className="muted">暂无部门申请数据</p>}
      </section>
      <section className="pilot-card analytics-card risk-card"><div className="section-heading"><div><span className="eyebrow">FOLLOW UP</span><h2>需要跟进</h2></div><AlertTriangle size={20} /></div>
        {riskItems.slice(0, 8).map(item => <RiskRow key={`${item.type}-${item.requestId}`} item={item} onOpen={onOpen} />)}
        {!riskItems.length && <div className="analytics-empty"><CheckCircle2 size={26} /><span>当前没有需要跟进的风险</span></div>}
      </section>
    </div>
  </>;
}

function Metric({ icon, label, value, hint, tone }: { icon: React.ReactNode; label: string; value: number | string; hint: string; tone: string }) {
  return <div className="metric-card analytics-metric"><span className={`metric-icon ${tone}`}>{icon}</span><div><span>{label}</span><strong>{value}</strong><small>{hint}</small></div></div>;
}

function decisionRate(item: AnalyticsBucket) {
  const decided = item.approved + item.rejected;
  return decided ? Math.round(item.approved / decided * 100) : "—";
}

function BucketBars({ items, max }: { items: AnalyticsBucket[]; max: number }) {
  return <div className="bucket-bars">{items.map(item => <div className="bucket-row" key={item.key}><div><strong>{item.label}</strong><span>{item.total} 条 · {item.hours} 小时</span></div><div className="bucket-track"><i style={{ width: `${item.total / max * 100}%` }} /></div><small>{item.pending ? `${item.pending} 待处理` : item.avgProcessingHours === null ? "暂无已完成" : `平均 ${item.avgProcessingHours}h`}</small></div>)}</div>;
}

function RiskRow({ item, onOpen }: { item: AnalyticsRisk; onOpen: (id: string) => Promise<void> }) {
  return <button className="analytics-risk" onClick={() => void onOpen(item.requestId)}><span className={`risk-dot ${item.type}`}><AlertTriangle size={14} /></span><span><strong>{item.title}</strong><small>{item.applicantName} · {item.department} · {statusLabels[item.status]}{item.approverName ? ` · ${item.approverName}` : ""}</small></span><b>{item.hours ? `${Math.round(item.hours)}h` : "查看"}</b></button>;
}
