export type LeaveType = "annual" | "personal" | "sick";
export type LeaveStatus =
  | "draft"
  | "submitted"
  | "validating"
  | "need_information"
  | "agent_reviewing"
  | "human_reviewing"
  | "approved"
  | "rejected"
  | "withdrawn"
  | "cancelled";

export interface User {
  id: string;
  username: string;
  employeeNo: string;
  name: string;
  department: string;
  departmentId: string;
  title: string;
  role: string;
  roleName: string;
  permissions: RolePermissions;
  managerId: string | null;
  noManager: boolean;
  leaveApproverId: string | null;
  hiredAt: string | null;
  status: "active" | "inactive";
  accountReady: boolean;
}

export interface Department {
  id: string;
  name: string;
  parentId: string | null;
  leaderId: string | null;
}

export interface Position {
  id: string;
  name: string;
}

export interface RolePermissions {
  manageAccounts: boolean;
  manageOrganization: boolean;
  leadTeam: boolean;
  approveLeave: boolean;
}

export interface PermissionRole {
  id: string;
  name: string;
  permissions: RolePermissions;
}

export interface OrganizationProfile {
  departmentPath: Department[];
  manager: User | null;
  managementChain: User[];
  directReports: User[];
  leaveApprover: User | null;
  approvalIssue: string | null;
  approvalReady: boolean;
}

export interface OrganizationAction {
  id: number;
  actorName: string;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
  createdAt: string;
}

export type EmployeeInput = Pick<User, "name" | "employeeNo" | "departmentId" | "title" | "role" | "managerId" | "noManager" | "leaveApproverId" | "hiredAt" | "status">;
export type OnboardInput = Omit<EmployeeInput, "status" | "hiredAt"> & {
  username: string;
  initialPassword: string;
  hiredAt: string;
  leaveEntitlements: Record<LeaveType, number>;
};
export interface DirectoryData { departments: Department[]; positions: Position[]; roles: PermissionRole[]; employees: User[] }
export interface EmployeeDetail { employee: User; profile: OrganizationProfile; actions: OrganizationAction[] }

export interface LeaveRequest {
  id: string;
  applicantId: string;
  applicantName: string;
  department: string;
  leaveType: LeaveType;
  startAt: string;
  endAt: string;
  durationHours: number;
  reason: string;
  handoverUser: string | null;
  handoverNotes: string | null;
  status: LeaveStatus;
  currentApproverId: string | null;
  currentApproverName: string | null;
  agentDecision: "approve" | "need_information" | "escalate" | null;
  agentReason: string | null;
  agentConfidence: number | null;
  version: number;
  createdAt: string;
  updatedAt: string;
  workSegments: [string, string][];
}

export interface ApprovalHistoryItem extends LeaveRequest {
  reviewDecision: "approved" | "rejected" | "returned" | "transferred" | "cancellation_approved" | "cancellation_rejected";
  reviewedAt: string;
  reviewComment: string;
}

export interface Balance {
  leaveType: LeaveType;
  totalHours: number;
  usedHours: number;
  remainingHours: number;
}

export interface AssistantConversation {
  id: string;
  messages: { role: "user" | "assistant"; content: string }[];
  draft: LeaveRequest | null;
  busy: boolean;
  runStatus: "idle" | "running" | "paused" | "completed";
}

export interface AssistantAction {
  id: string;
  operation: string;
  targetId: string | null;
  preview: { title: string; fields: { label: string; value: string }[]; note: string };
  status: "pending" | "executing" | "succeeded" | "failed" | "cancelled";
  createdAt?: string;
  updatedAt?: string;
  requiresPassword?: boolean;
  editableData?: {
    leaveType: LeaveType; startAt: string; endAt: string; reason: string;
    handoverUser: string | null; handoverNotes: string | null;
  } | null;
  result?: { message: string; leave?: LeaveRequest } | null;
}

export type ChatCard =
  | { kind: "requests"; title: string; items: LeaveRequest[]; total: number }
  | ({ kind: "request"; title: string } & LeaveDetail)
  | { kind: "cancellations"; title: string; items: CancellationInboxItem[] }
  | ({ kind: "ledger"; title: string; employeeId: string } & LedgerData)
  | { kind: "calendar"; title: string; timezone?: string; defaultWorkdays?: string[]; defaultSessions?: string[];
      today?: { day: string; isWorkday: boolean; source: string }; interpretation?: string; days: CalendarDay[] }
  | { kind: "notifications"; title: string; items: Notification[]; unread: number }
  | ({ kind: "organization"; title: string } & DirectoryData)
  | ({ kind: "employee"; title: string } & EmployeeDetail);
export interface AgentProfile {
  instanceId: string;
  profileId: string;
  profileVersion: string;
  name: string;
  mission: string;
  tools: string[];
  skills: { id: string; name: string }[];
  scope: { department: string; ledDepartments: string[]; directReportCount: number };
}
export interface ChatWorkspace extends AssistantConversation {
  context: LeaveDetail | null;
  action: AssistantAction | null;
  cards: ChatCard[];
  agent: AgentProfile;
  clarification?: { message: string; missingFields: string[]; choices: string[] } | null;
}
export interface ChatSchema {
  type?: string; enum?: (string | number)[]; const?: string | number; anyOf?: ChatSchema[]; $ref?: string;
  properties?: Record<string, ChatSchema>; items?: ChatSchema; required?: string[]; $defs?: Record<string, ChatSchema>;
  default?: unknown; minimum?: number; maximum?: number; minItems?: number; maxItems?: number;
}
export interface ChatOperation { operation: string; title: string; target: string | null; schema: ChatSchema; labels?: Record<string, string> }
export interface ChatCatalog { items: ChatOperation[]; labels: Record<string, string>; directory: DirectoryData; agent: AgentProfile }

export interface BootstrapData {
  currentUser: User;
  organization: OrganizationProfile;
  users: User[];
  balances: Balance[];
  mine: LeaveRequest[];
  inbox: LeaveRequest[];
  approvalHistory: ApprovalHistoryItem[];
  cancellationInbox: CancellationInboxItem[];
  unreadNotifications: number;
  approvalMode: "assist";
  stats: {
    pending: number;
    approved: number;
    inProgress: number;
    agentHandled: number;
    expensePending: number;
    expenseApproved: number;
    travelPending: number;
    travelApproved: number;
    procurementPending: number;
    procurementApproved: number;
    overtimePending: number;
    overtimeApproved: number;
  };
  expenses: ExpenseLists;
  travelRequests: TravelLists;
  procurementRequests: ProcurementLists;
  overtimeRequests: { overtime: OvertimeLists; compTime: OvertimeLists; balance: number };
  agentMode: "deep-agent" | "agent-unavailable";
  agentConfig: {
    protocol: "openai-compatible";
    model: string;
    baseUrl: string;
  };
}

export type ExpenseStatus = "draft" | "human_reviewing" | "approved" | "rejected" | "withdrawn";
export type ExpenseCategory = "travel" | "meal" | "office" | "software" | "other";
export type ExpensePaymentMethod = "personal" | "corporate_card" | "cash" | "other";
export interface ExpenseRequest {
  id: string;
  applicantId: string;
  applicantName: string;
  department: string;
  category: ExpenseCategory;
  amount: number;
  currency: "CNY" | "USD" | "EUR";
  occurredAt: string;
  description: string;
  paymentMethod: ExpensePaymentMethod;
  travelRequestId: string | null;
  travelDestination: string | null;
  status: ExpenseStatus;
  currentApproverId: string | null;
  currentApproverName: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}
export interface ExpenseAction { id: number; requestId: string; actorId: string; actorName: string; action: string; reason: string; fromStatus: string | null; toStatus: ExpenseStatus; createdAt: string; }
export interface ExpenseLists { mine: ExpenseRequest[]; inbox: ExpenseRequest[]; history: ExpenseRequest[]; }

export type TravelStatus = "draft" | "human_reviewing" | "approved" | "rejected" | "withdrawn";
export type TravelTransportStandard = "economy" | "high_speed" | "business";
export type TravelAccommodationStandard = "none" | "standard" | "premium";
export interface TravelRequest {
  id: string;
  applicantId: string;
  applicantName: string;
  department: string;
  destination: string;
  purpose: string;
  startAt: string;
  endAt: string;
  travelerIds: string[];
  travelerNames: string[];
  transportStandard: TravelTransportStandard;
  accommodationStandard: TravelAccommodationStandard;
  status: TravelStatus;
  currentApproverId: string | null;
  currentApproverName: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}
export interface TravelAction { id: number; requestId: string; actorId: string; actorName: string; action: string; reason: string; fromStatus: string | null; toStatus: TravelStatus; createdAt: string; }
export interface TravelLists { mine: TravelRequest[]; inbox: TravelRequest[]; history: TravelRequest[]; }

export type ProcurementStatus = "draft" | "human_reviewing" | "approved" | "rejected" | "withdrawn";
export interface ProcurementItem { name: string; quantity: number; unitPrice: number; }
export interface ProcurementRequest {
  id: string; applicantId: string; applicantName: string; department: string;
  title: string; purpose: string; items: ProcurementItem[]; budget: number; estimatedAmount: number;
  currency: "CNY" | "USD" | "EUR"; supplier: string | null; needBy: string;
  status: ProcurementStatus; currentApproverId: string | null; currentApproverName: string | null;
  version: number; createdAt: string; updatedAt: string;
}
export interface ProcurementAction { id: number; requestId: string; actorId: string; actorName: string; action: string; reason: string; fromStatus: string | null; toStatus: ProcurementStatus; createdAt: string; }
export interface ProcurementLists { mine: ProcurementRequest[]; inbox: ProcurementRequest[]; history: ProcurementRequest[]; }

export type OvertimeStatus = "draft" | "human_reviewing" | "approved" | "rejected" | "withdrawn";
export type OvertimeCompensation = "comp_leave" | "pay";
export interface OvertimeRequest {
  id: string; applicantId: string; applicantName: string; department: string;
  startAt: string; endAt: string; hours: number; reason: string; compensationType: OvertimeCompensation;
  status: OvertimeStatus; currentApproverId: string | null; currentApproverName: string | null;
  version: number; createdAt: string; updatedAt: string;
}
export interface CompTimeRequest {
  id: string; applicantId: string; applicantName: string; department: string;
  date: string; hours: number; reason: string; status: OvertimeStatus;
  currentApproverId: string | null; currentApproverName: string | null; version: number; createdAt: string; updatedAt: string;
}
export interface OvertimeLists { mine: (OvertimeRequest | CompTimeRequest)[]; inbox: (OvertimeRequest | CompTimeRequest)[]; history: (OvertimeRequest | CompTimeRequest)[]; }

export interface AnalyticsBucket {
  key: string;
  label: string;
  total: number;
  pending: number;
  approved: number;
  rejected: number;
  hours: number;
  avgProcessingHours: number | null;
}

export interface ApprovalAnalytics {
  generatedAt: string;
  scope: "organization";
  summary: {
    total: number;
    pending: number;
    approved: number;
    rejected: number;
    draft: number;
    withdrawn: number;
    cancelled: number;
    totalHours: number;
    avgProcessingHours: number | null;
    approvalRate: number | null;
    overdue: number;
    aiReviewed: number;
    aiEscalated: number;
  };
  trend: { period: string; submitted: number; approved: number; rejected: number }[];
  byDepartment: AnalyticsBucket[];
  byLeaveType: AnalyticsBucket[];
  risks: {
    overdue: AnalyticsRisk[];
    missingApprover: AnalyticsRisk[];
    aiEscalated: AnalyticsRisk[];
  };
}

export interface AnalyticsRisk {
  type: string;
  title: string;
  requestId: string;
  applicantName: string;
  department: string;
  status: LeaveStatus;
  hours: number;
  approverName: string | null;
}

export interface ApprovalAction {
  id: number;
  actorType: "human" | "agent" | "system";
  actorName: string;
  action: string;
  reason: string;
  toStatus: LeaveStatus;
  createdAt: string;
}

export interface ApprovalStep {
  id: number;
  requestId: string;
  stepOrder: number;
  approverId: string;
  approverName: string;
  approverTitle: string;
  status: "pending" | "approved" | "rejected" | "skipped";
  comment: string | null;
  reviewedAt: string | null;
  createdAt: string;
}

export interface LeaveDetail {
  leave: LeaveRequest;
  actions: ApprovalAction[];
  approvalSteps: ApprovalStep[];
  attachments: Attachment[];
  cancellations: Cancellation[];
  review: Review | null;
}

export interface Attachment { id: string; filename: string; size: number; mimeType: string; createdAt: string }
export interface Cancellation {
  id: string; requestId: string; startAt: string; endAt: string; hours: number; reason: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  approverId: string; approverName: string; comment: string | null; createdAt: string;
}
export interface CancellationInboxItem extends Cancellation { applicantId: string; applicantName: string; leaveType: LeaveType }
export interface Review { summary: string; reason: string; riskFlags: string[]; missingFields: string[]; policyReferences: string[] }
export interface CalendarDay { day: string; isWorkday: number; name: string }
export interface LedgerEntry { id: number; leaveType: LeaveType; totalDelta: number; usedDelta: number; reason: string; actorName: string; createdAt: string; requestId: string | null }
export interface LedgerData { balances: Balance[]; entries: LedgerEntry[] }
export interface Notification { id: number; requestId: string | null; title: string; body: string; createdAt: string; readAt: string | null }
export type LeaveFormInput = Pick<LeaveRequest, "leaveType" | "startAt" | "endAt" | "reason" | "handoverUser" | "handoverNotes">;

export const API_BASE = import.meta.env.VITE_API_BASE ?? "";
let csrfToken = "";

export interface AuthSession { user: User; csrfToken: string; mustChangePassword: boolean }
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export function clearAuth() { csrfToken = ""; }

async function authRequest(path: string, input?: object): Promise<AuthSession> {
  const session = await request<AuthSession>(path, "", input ? { method: "POST", body: JSON.stringify(input) } : undefined);
  csrfToken = session.csrfToken;
  return session;
}

export async function request<T>(path: string, _userId: string, init?: RequestInit): Promise<T> {
  const hasBody = init?.body !== undefined && init.body !== null;
  const requestToken = csrfToken;
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: {
      ...(csrfToken ? { "x-csrf-token": csrfToken } : {}),
      ...(hasBody ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const text = await response.text();
  let body: (T & { message?: string }) | undefined;
  try { body = text ? JSON.parse(text) : undefined; }
  catch { throw new ApiError("服务暂不可用，请稍后重试", response.status); }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/") && requestToken === csrfToken) {
      clearAuth(); window.dispatchEvent(new Event("oa-session-expired"));
    }
    throw new ApiError(body?.message ?? "请求失败", response.status);
  }
  return body as T;
}

export const api = {
  session: () => authRequest("/api/auth/session"),
  login: (username: string, password: string) => authRequest("/api/auth/login", { username, password }),
  changePassword: (currentPassword: string, newPassword: string) => authRequest("/api/auth/password", { currentPassword, newPassword }),
  logout: () => request<void>("/api/auth/logout", "", { method: "POST" }),
  resetPassword: (employeeId: string, initialPassword: string) => request<void>(`/api/employees/${encodeURIComponent(employeeId)}/password`, "", { method: "POST", body: JSON.stringify({ initialPassword }) }),
  bootstrap: (userId: string) => request<BootstrapData>("/api/bootstrap", userId),
  organization: (userId: string) => request<DirectoryData>("/api/organization", userId),
  createPosition: (userId: string, name: string) => request<{ position: Position }>("/api/positions", userId, { method: "POST", body: JSON.stringify({ name }) }),
  createRole: (userId: string, input: Omit<PermissionRole, "id">) => request<{ role: PermissionRole }>("/api/roles", userId, { method: "POST", body: JSON.stringify(input) }),
  employee: (userId: string, id: string) => request<EmployeeDetail>(`/api/employees/${encodeURIComponent(id)}`, userId),
  onboard: (userId: string, input: OnboardInput) => request<EmployeeDetail>("/api/employees", userId, { method: "POST", body: JSON.stringify(input) }),
  updateEmployee: (userId: string, id: string, input: EmployeeInput) => request<EmployeeDetail>(`/api/employees/${encodeURIComponent(id)}`, userId, { method: "PUT", body: JSON.stringify(input) }),
  saveDepartment: (userId: string, input: Omit<Department, "id">, id?: string) => request<{ department: Department }>(id ? `/api/departments/${encodeURIComponent(id)}` : "/api/departments", userId, { method: id ? "PUT" : "POST", body: JSON.stringify(input) }),
  departmentActions: (userId: string, id: string) => request<{ actions: OrganizationAction[] }>(`/api/departments/${encodeURIComponent(id)}/actions`, userId),

  createLeave: (
    userId: string,
    input: {
      leaveType: LeaveType;
      startAt: string;
      endAt: string;
      reason: string;
      handoverUser?: string;
      handoverNotes?: string;
    },
  ) => request<{ leave: LeaveRequest }>("/api/leave-requests", userId, { method: "POST", body: JSON.stringify(input) }),

  conversation: (userId: string, id: string) => request<AssistantConversation>(`/api/agent/conversations/${encodeURIComponent(id)}`, userId),

  agentMessage: (userId: string, message: string, conversationId?: string, contextId?: string, actionId?: string) =>
    request<{ message: string; conversation: ChatWorkspace }>("/api/agent/messages", userId, {
      method: "POST", body: JSON.stringify({ message, conversationId, contextId, actionId }),
    }),
  chatWorkspace: (userId: string, id: string) => request<ChatWorkspace>("/api/agent/workspace/" + encodeURIComponent(id), userId),
  createChatWorkspace: (userId: string) => request<ChatWorkspace>("/api/agent/workspace", userId, { method: "POST" }),
  pauseAgentRun: (userId: string, id: string) => request<ChatWorkspace>("/api/agent/workspace/" + encodeURIComponent(id) + "/pause", userId, { method: "POST" }),
  chatCatalog: () => request<ChatCatalog>("/api/agent/catalog", ""),
  prepareChat: (conversationId: string, operation: string, targetId: string | null, data: Record<string, unknown>) =>
    request<ChatWorkspace>("/api/agent/prepare", "", { method: "POST", body: JSON.stringify({ conversationId, operation, targetId, data }) }),
  confirmAgentAction: (userId: string, id: string, initialPassword?: string) =>
    request<ChatWorkspace>("/api/agent/actions/" + encodeURIComponent(id) + "/confirm", userId, { method: "POST", body: JSON.stringify({ initialPassword }) }),
  cancelAgentAction: (userId: string, id: string) =>
    request<ChatWorkspace>("/api/agent/actions/" + encodeURIComponent(id) + "/cancel", userId, { method: "POST" }),
  chatUpload: (conversationId: string, file: File) => request<ChatWorkspace>("/api/agent/workspace/" + encodeURIComponent(conversationId) + "/attachments", "", {
    method: "POST", body: file, headers: { "content-type": "application/octet-stream", "x-filename": encodeURIComponent(file.name) },
  }),

  agentDraft: (userId: string, message: string, conversationId?: string) =>
    request<{
      conversation: AssistantConversation;
      status: "draft_created" | "needs_information" | "approval_completed" | "rejection_completed" | "no_action";
      leave?: LeaveRequest;
      approvedLeaves?: LeaveRequest[];
      rejectedLeaves?: LeaveRequest[];
      missingFields?: string[];
      extraction: Record<string, unknown> | null;
      message: string;
      execution: {
        mode: "deep-agent" | "agent-unavailable";
        tool: "create_leave_draft" | "list_pending_leave_approvals" | "approve_leave_request" | "reject_leave_request" | null;
        endpoint: string | null;
        toolCalls: Array<{ tool: string; endpoint: string; requestId?: string }>;
      };
    }>("/api/agent/leave/draft", userId, { method: "POST", body: JSON.stringify({ message, conversationId }) }),

  submit: (userId: string, id: string, version?: number) =>
    request<LeaveDetail>(`/api/leave-requests/${id}/submit`, userId, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),

  approve: (userId: string, id: string, reason: string, version?: number) =>
    request<LeaveDetail>(`/api/leave-requests/${id}/approve`, userId, {
      method: "POST",
      body: JSON.stringify({ reason, version }),
    }),

  reject: (userId: string, id: string, reason: string, version?: number) =>
    request<LeaveDetail>(`/api/leave-requests/${id}/reject`, userId, {
      method: "POST",
      body: JSON.stringify({ reason, version }),
    }),

  deleteLeave: (userId: string, id: string) =>
    request<void>(`/api/leave-requests/${id}`, userId, { method: "DELETE" }),

  details: (userId: string, id: string) =>
    request<LeaveDetail>(`/api/leave-requests/${id}`, userId),
  approvalAnalytics: (userId: string) => request<ApprovalAnalytics>("/api/analytics/approval", userId),
  expenses: (userId: string) => request<ExpenseLists>("/api/expenses", userId),
  createExpense: (userId: string, input: Omit<ExpenseRequest, "id" | "applicantId" | "applicantName" | "department" | "status" | "currentApproverId" | "currentApproverName" | "version" | "createdAt" | "updatedAt" | "travelDestination">) => request<{ expense: ExpenseRequest }>("/api/expenses", userId, { method: "POST", body: JSON.stringify(input) }),
  submitExpense: (userId: string, id: string, version?: number) => request<{ expense: ExpenseRequest }>(`/api/expenses/${id}/submit`, userId, { method: "POST", body: JSON.stringify({ version }) }),
  decideExpense: (userId: string, id: string, decision: "approve" | "reject", reason: string, version?: number) => request<{ expense: ExpenseRequest }>(`/api/expenses/${id}/${decision}`, userId, { method: "POST", body: JSON.stringify({ reason, version }) }),
  withdrawExpense: (userId: string, id: string, reason: string) => request<{ expense: ExpenseRequest }>(`/api/expenses/${id}/withdraw`, userId, { method: "POST", body: JSON.stringify({ reason }) }),
  travelRequests: (userId: string) => request<TravelLists>("/api/travel-requests", userId),
  createTravel: (userId: string, input: { destination: string; purpose: string; startAt: string; endAt: string; travelerIds: string[]; transportStandard: TravelTransportStandard; accommodationStandard: TravelAccommodationStandard }) => request<{ travel: TravelRequest }>("/api/travel-requests", userId, { method: "POST", body: JSON.stringify(input) }),
  submitTravel: (userId: string, id: string, version?: number) => request<{ travel: TravelRequest }>(`/api/travel-requests/${id}/submit`, userId, { method: "POST", body: JSON.stringify({ version }) }),
  decideTravel: (userId: string, id: string, decision: "approve" | "reject", reason: string, version?: number) => request<{ travel: TravelRequest }>(`/api/travel-requests/${id}/${decision}`, userId, { method: "POST", body: JSON.stringify({ reason, version }) }),
  withdrawTravel: (userId: string, id: string, reason: string) => request<{ travel: TravelRequest }>(`/api/travel-requests/${id}/withdraw`, userId, { method: "POST", body: JSON.stringify({ reason }) }),
  procurementRequests: (userId: string) => request<ProcurementLists>("/api/procurement-requests", userId),
  createProcurement: (userId: string, input: { title: string; purpose: string; items: ProcurementItem[]; budget: number; currency: "CNY" | "USD" | "EUR"; supplier?: string | null; needBy: string }) => request<{ procurement: ProcurementRequest }>("/api/procurement-requests", userId, { method: "POST", body: JSON.stringify(input) }),
  submitProcurement: (userId: string, id: string, version?: number) => request<{ procurement: ProcurementRequest }>(`/api/procurement-requests/${id}/submit`, userId, { method: "POST", body: JSON.stringify({ version }) }),
  decideProcurement: (userId: string, id: string, decision: "approve" | "reject", reason: string, version?: number) => request<{ procurement: ProcurementRequest }>(`/api/procurement-requests/${id}/${decision}`, userId, { method: "POST", body: JSON.stringify({ reason, version }) }),
  withdrawProcurement: (userId: string, id: string, reason: string) => request<{ procurement: ProcurementRequest }>(`/api/procurement-requests/${id}/withdraw`, userId, { method: "POST", body: JSON.stringify({ reason }) }),
  overtimeRequests: (userId: string) => request<{ overtime: OvertimeLists; compTime: OvertimeLists; balance: number }>("/api/overtime", userId),
  createOvertime: (userId: string, input: { startAt: string; endAt: string; reason: string; compensationType: OvertimeCompensation }) => request<{ overtime: OvertimeRequest }>("/api/overtime", userId, { method: "POST", body: JSON.stringify(input) }),
  submitOvertime: (userId: string, id: string, version?: number) => request<{ overtime: OvertimeRequest }>(`/api/overtime/${id}/submit`, userId, { method: "POST", body: JSON.stringify({ version }) }),
  decideOvertime: (userId: string, id: string, decision: "approve" | "reject", reason: string, version?: number) => request<{ overtime: OvertimeRequest }>(`/api/overtime/${id}/${decision}`, userId, { method: "POST", body: JSON.stringify({ reason, version }) }),
  withdrawOvertime: (userId: string, id: string, reason: string) => request<{ overtime: OvertimeRequest }>(`/api/overtime/${id}/withdraw`, userId, { method: "POST", body: JSON.stringify({ reason }) }),
  createCompTime: (userId: string, input: { date: string; hours: number; reason: string }) => request<{ compTime: CompTimeRequest }>("/api/comp-time", userId, { method: "POST", body: JSON.stringify(input) }),
  submitCompTime: (userId: string, id: string, version?: number) => request<{ compTime: CompTimeRequest }>(`/api/comp-time/${id}/submit`, userId, { method: "POST", body: JSON.stringify({ version }) }),
  decideCompTime: (userId: string, id: string, decision: "approve" | "reject", reason: string, version?: number) => request<{ compTime: CompTimeRequest }>(`/api/comp-time/${id}/${decision}`, userId, { method: "POST", body: JSON.stringify({ reason, version }) }),
  withdrawCompTime: (userId: string, id: string, reason: string) => request<{ compTime: CompTimeRequest }>(`/api/comp-time/${id}/withdraw`, userId, { method: "POST", body: JSON.stringify({ reason }) }),
};

export const leaveTypeLabels: Record<LeaveType, string> = { annual: "年假", personal: "事假", sick: "病假" };
export const statusLabels: Record<LeaveStatus, string> = {
  draft: "草稿",
  submitted: "已提交",
  validating: "校验中",
  need_information: "待补充",
  agent_reviewing: "智能审核中",
  human_reviewing: "人工审批中",
  approved: "已通过",
  rejected: "已驳回",
  withdrawn: "已撤回",
  cancelled: "已销假",
};

const post = <T,>(path: string, body: object = {}) => request<T>(path, "", { method: "POST", body: JSON.stringify(body) });
export const pilotApi = {
  edit: (id: string, body: LeaveFormInput & { version: number }) => request<LeaveDetail>(`/api/leave-requests/${id}`, "", { method: "PUT", body: JSON.stringify(body) }),
  action: (id: string, action: "withdraw" | "request-information" | "transfer", body: object) => post<LeaveDetail>(`/api/leave-requests/${id}/${action}`, body),
  cancel: (id: string, body: object) => post<LeaveDetail>(`/api/leave-requests/${id}/cancellations`, body),
  cancelAction: (id: string, action: string, body: object) => post<LeaveDetail>(`/api/cancellations/${id}/${action}`, body),
  upload: (id: string, file: File) => request<LeaveDetail>(`/api/leave-requests/${id}/attachments`, "", { method: "POST", body: file, headers: { "content-type": "application/octet-stream", "x-filename": encodeURIComponent(file.name) } }),
  remind: (id: string) => post<void>(`/api/leave-requests/${id}/remind`),
  removeAttachment: (id: string) => request<LeaveDetail>(`/api/attachments/${id}`, "", { method: "DELETE" }),
  calendar: () => request<{ days: CalendarDay[] }>("/api/work-calendar", ""),
  saveDay: (body: { day: string; isWorkday: boolean; name: string }) => request<{ days: CalendarDay[] }>("/api/work-calendar", "", { method: "PUT", body: JSON.stringify(body) }),
  resetDay: (day: string) => request<void>(`/api/work-calendar/${day}`, "", { method: "DELETE" }),
  ledger: (id: string) => request<LedgerData>(`/api/balance-ledger/${id}`, ""),
  adjust: (body: object) => post<LedgerData>("/api/balance-adjustments", body),
  notifications: () => request<{ items: Notification[]; unread: number }>("/api/notifications", ""),
  read: (id?: number) => post<void>(id === undefined ? "/api/notifications/read" : `/api/notifications/${id}/read`),
  adminRequests: () => request<{ items: LeaveRequest[] }>("/api/pilot/requests", ""),
};
