import { request } from '@umijs/max';
import type { Review, ReviewInput, TaskStatus } from '@/services/risk';

/**
 * 机会中心 IP 专家（change add-opportunity-ip-agent，#180）。
 *
 * 会产生外部调用的只有五个动作：生成检索方案（大模型）、执行检索（MCP）、取证（MCP）、
 * 生成风险分析（大模型）、对话追问（大模型）。它们只写任务、返回 202，由后台执行；定时复检
 * 到点由后台入队检索。其余接口只读写库，刷新与轮询不会产生任何外部调用。
 */

export type IpTaskKind = 'plan' | 'search' | 'detail' | 'analyze' | 'ask';
export type DetailTool = 'get_patent_legal_status' | 'bibliography' | 'abstract_image';
export type QueryStatus = 'success' | 'failed' | 'unknown';
export type Relevance = 'high' | 'medium' | 'low' | 'unclear';

export interface IpMeta {
  mcp_configured: boolean;
  llm_configured: boolean;
  llm_model: string | null;
  markets: { code: string; name: string }[];
  jurisdictions: { code: string; name: string; note: string }[];
  recommendations: Record<string, string[]>;
  query_limits: number[];
  max_queries: number;
  max_detail_batch: number;
  detail_tools: DetailTool[];
  budget: { per_research: number; daily_limit: number; daily_used: number };
}

export interface IpTask {
  id: number;
  kind: IpTaskKind;
  status: TaskStatus;
  error_category: string | null;
  planned_mcp_calls: number;
  /** 由定时复检入队 */
  scheduled: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ResearchSummary {
  id: number;
  product_name: string;
  markets: string[];
  jurisdictions: string[];
  workspace_id: number | null;
  created_at: string;
  candidate_count: number;
  reviewed_markets: number;
  last_task: { kind: IpTaskKind; status: TaskStatus } | null;
  /** null = 没有定时复检；true/false = 启用/暂停 */
  schedule_enabled: boolean | null;
}

export interface IpMessage {
  id: number;
  task_id: number;
  role: 'user' | 'agent';
  content: string;
  model: string | null;
  cited_candidate_ids: number[];
  created_at: string;
}

export interface IpSchedule {
  id: number;
  research_id: number;
  frequency: 'weekly' | 'monthly';
  weekday: number | null;
  day_of_month: number | null;
  hour: number;
  queries: { query_text: string; limit: number }[];
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
  last_status: 'queued' | 'skipped' | null;
  last_reason: string | null;
  last_task_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleInput {
  frequency: 'weekly' | 'monthly';
  weekday?: number | null;
  day_of_month?: number | null;
  hour: number;
  queries: { query_text: string; limit: number }[];
  enabled: boolean;
}

export interface Workspace {
  id: number;
  name: string;
  task_count: number;
  created_at: string;
}

export interface PlanContent {
  queries: { query_text: string; rationale: string }[];
  assumptions: string[];
}

export interface AnalysisContent {
  summary: string;
  signals: { candidate_id: number; relevance: Relevance; reason: string }[];
  coverage_gaps: string[];
  unknowns: string[];
  next_steps: string[];
}

export interface Coverage {
  queries_total: number;
  queries_success: number;
  queries_failed: number;
  queries_unknown: number;
  candidates: number;
  detailed: Record<DetailTool, number>;
}

export interface AgentOutput<T> {
  id: number;
  task_id: number;
  model: string;
  content: T;
  coverage: Coverage | null;
  created_at: string;
}

export interface IpQuery {
  id: number;
  task_id: number;
  query_text: string;
  limit: number;
  status: QueryStatus;
  error_category: string | null;
  total_count: number | null;
  returned_count: number | null;
  fetched_at: string;
}

export interface IpDetailEntry {
  status: 'success' | 'failed' | 'unknown' | 'missing';
  error_category: string | null;
  fetched_at: string;
  data: Record<string, any> | null;
  has_image: boolean;
}

export interface IpCandidate {
  id: number;
  query_id: number;
  rank: number;
  patent_id: string;
  pn: string | null;
  title: string | null;
  authority: string | null;
  apno: string | null;
  apdt: number | null;
  pbdt: number | null;
  assignee: string | null;
  fetched_at: string;
  details: Partial<Record<DetailTool, IpDetailEntry>>;
}

export interface ResearchDetail {
  id: number;
  product_name: string;
  description: string | null;
  keywords: string[];
  loc_classes: string[];
  jurisdictions: string[];
  markets: string[];
  workspace_id: number | null;
  created_at: string;
  jurisdiction_details: { code: string; name: string; note: string }[];
  mcp_configured: boolean;
  llm_configured: boolean;
  budget: { per_research: number; used: number; daily_limit: number; daily_used: number };
  active_task: IpTask | null;
  tasks: IpTask[];
  plans: AgentOutput<PlanContent>[];
  analyses: AgentOutput<AnalysisContent>[];
  queries: IpQuery[];
  candidates: IpCandidate[];
  coverage: Coverage;
  search_done: boolean;
  reviews: Record<string, Review[]>;
  calls: { provider: 'mcp' | 'llm'; tool: string; outcome: string; count: number }[];
  messages: IpMessage[];
  schedule: IpSchedule | null;
}

export interface Accepted {
  task_id: number;
  status: TaskStatus;
}

export interface CreateResearchInput {
  client_token: string;
  product_name: string;
  description?: string | null;
  keywords: string[];
  loc_classes: string[];
  jurisdictions: string[];
  markets: string[];
  workspace_id?: number | null;
}

const BASE = '/api/opportunity/ip';

export async function getIpMeta() {
  return request<IpMeta>(`${BASE}/meta`, { method: 'GET' });
}

export async function createResearch(body: CreateResearchInput) {
  return request<{ id: number }>(`${BASE}/researches`, { method: 'POST', data: body });
}

export async function listResearches(params: {
  page: number;
  page_size: number;
  workspace_id?: number;
  unassigned?: boolean;
}) {
  return request<{ total: number; page: number; page_size: number; items: ResearchSummary[] }>(
    `${BASE}/researches`,
    { method: 'GET', params },
  );
}

export async function getResearch(id: number) {
  return request<ResearchDetail>(`${BASE}/researches/${id}`, { method: 'GET' });
}

export async function requestPlan(id: number, client_token: string) {
  return request<Accepted>(`${BASE}/researches/${id}/plan`, { method: 'POST', data: { client_token } });
}

export async function requestSearch(
  id: number,
  body: { client_token: string; queries: { query_text: string; limit: number }[] },
) {
  return request<Accepted>(`${BASE}/researches/${id}/searches`, { method: 'POST', data: body });
}

export async function requestDetails(
  id: number,
  body: { client_token: string; candidate_ids: number[]; tools: DetailTool[] },
) {
  return request<Accepted>(`${BASE}/researches/${id}/details`, { method: 'POST', data: body });
}

export async function requestAnalysis(id: number, client_token: string) {
  return request<Accepted>(`${BASE}/researches/${id}/analysis`, { method: 'POST', data: { client_token } });
}

/** 附图要带登录凭证读取，不能直接作为 <img src>；取回 Blob 再转对象 URL。 */
export async function getCandidateImage(id: number, candidateId: number) {
  return request<Blob>(`${BASE}/researches/${id}/candidates/${candidateId}/image`, {
    method: 'GET',
    responseType: 'blob',
  });
}

export async function addIpReview(id: number, body: ReviewInput) {
  return request<Review>(`${BASE}/researches/${id}/reviews`, { method: 'POST', data: body });
}

export async function moveResearch(id: number, workspace_id: number | null) {
  return request<{ id: number; workspace_id: number | null }>(`${BASE}/researches/${id}/workspace`, {
    method: 'PATCH',
    data: { workspace_id },
  });
}

/** 对话追问：只调用大模型，不调用智慧芽、不计 MCP 预算。 */
export async function askResearch(id: number, body: { client_token: string; content: string }) {
  return request<Accepted>(`${BASE}/researches/${id}/messages`, { method: 'POST', data: body });
}

export async function listSchedules() {
  return request<{ items: (IpSchedule & { product_name: string; workspace_id: number | null })[] }>(
    `${BASE}/schedules`,
    { method: 'GET' },
  );
}

export async function saveSchedule(id: number, body: ScheduleInput) {
  return request<IpSchedule>(`${BASE}/researches/${id}/schedule`, { method: 'PUT', data: body });
}

export async function deleteSchedule(id: number) {
  return request<void>(`${BASE}/researches/${id}/schedule`, { method: 'DELETE' });
}

const WS = '/api/opportunity/workspaces';

export async function listWorkspaces() {
  return request<{ items: Workspace[]; unassigned_count: number; total_count: number }>(WS, { method: 'GET' });
}

export async function createWorkspace(name: string) {
  return request<Workspace>(WS, { method: 'POST', data: { name } });
}

export async function renameWorkspace(id: number, name: string) {
  return request<Workspace>(`${WS}/${id}`, { method: 'PATCH', data: { name } });
}

export async function deleteWorkspace(id: number) {
  return request<void>(`${WS}/${id}`, { method: 'DELETE' });
}
