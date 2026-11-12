import { request } from '@umijs/max';

/**
 * 外观专利核查（change add-patent-risk-check，#24）。
 *
 * 会计费的动作只有四个：创建核查、读取下一页、补充详情（著录/法律状态/全部附图/公报）、重新检索——都只写任务、返回 202，
 * 由后台调用智慧芽。其余接口只读库，刷新、轮询不会产生任何外部调用。
 */

export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'unknown';
export type DetailKind = 'bibliography' | 'legal' | 'drawings' | 'pdf';
export type FileDetailKind = 'drawings' | 'pdf';
export type DetailStatus = 'success' | 'failed' | 'unknown' | 'missing';
export type RiskLevel = 'high' | 'medium' | 'low' | 'undetermined';
export type Decision = 'pass' | 'conditional_pass' | 'continue_verify' | 'need_evidence' | 'not_recommended';

export interface RiskMeta {
  provider: string;
  provider_configured: boolean;
  markets: { code: string; name: string }[];
  jurisdictions: { code: string; name: string; note: string; verified: boolean }[];
  recommendations: Record<string, string[]>;
  page_size_options: number[];
  default_page_size: number;
  max_offset: number;
  max_detail_batch: number;
  /** 含全部附图或公报 PDF 时一次最多几件 */
  max_file_detail_batch: number;
  /** OSS 存档是否启用；未启用时附图与公报只保留供应商签名链接 */
  evidence_archive_enabled: boolean;
  max_image_bytes: number;
}

export interface CheckSummary {
  id: number;
  product_name: string;
  product_version: string;
  markets: string[];
  jurisdictions: string[];
  search_status: TaskStatus;
  fetched_count: number;
  total_count: number | null;
  created_at: string;
  searched_at: string | null;
  reviewed_markets?: number;
}

export interface DetailEntry {
  status: DetailStatus;
  error_category: string | null;
  fetched_at: string;
  data: Record<string, any> | null;
}

/** 全部附图 / 公报 PDF 中的一个文件。已存档的经鉴权端点读取；未存档的只有限期签名链接。 */
export interface EvidenceFile {
  seq: number;
  image_type: string | null;
  archived: boolean;
  content_type: string | null;
  size_bytes: number | null;
  archived_at: string | null;
  archive_error: string | null;
  link_expires_at: string | null;
  /** 仅未存档、可信且未过期时给出 */
  link_url: string | null;
}

export interface FileDetailData {
  pn: string | null;
  total: number | null;
  files: EvidenceFile[];
}

export interface Candidate {
  id: number;
  rank: number;
  patent_id: string;
  patent_pn: string | null;
  title: string | null;
  image_url: string | null;
  score: number | null;
  authority: string | null;
  apno: string | null;
  apdt: number | null;
  pbdt: number | null;
  assignee: string | null;
  loc: string[] | null;
  fetched_at: string;
  image_maybe_expired: boolean;
  details: Partial<Record<DetailKind, DetailEntry>>;
}

export interface Review {
  id: number;
  market: string;
  risk_level: RiskLevel;
  decision: Decision;
  basis: string;
  uncovered_scope: string;
  conditions: string | null;
  evidence_candidate_ids: number[];
  review_date: string | null;
  signer_id: number;
  signer_name: string | null;
  created_at: string;
}

export interface TaskRow {
  id: number;
  kind: 'search' | 'detail';
  status: TaskStatus;
  error_category: string | null;
  params: Record<string, any>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CheckDetail extends CheckSummary {
  source_ref: string | null;
  description: string | null;
  page_size: number;
  previous_check_id: number | null;
  next_check_ids: number[];
  provider_configured: boolean;
  jurisdiction_details: { code: string; name: string; note: string; verified: boolean }[];
  search_completed: boolean;
  can_fetch_more: boolean;
  can_retry: boolean;
  active_kinds: ('search' | 'detail')[];
  tasks: TaskRow[];
  pages: {
    id: number;
    offset: number;
    limit: number;
    returned_count: number;
    total_count: number;
    fetched_at: string;
    task_id: number;
  }[];
  candidates: Candidate[];
  reviews: Record<string, Review[]>;
  calls: { api: string; outcome: string; count: number }[];
}

export interface TaskAccepted {
  task_id: number | null;
  status: TaskStatus;
  skipped?: boolean;
}

export interface CreateCheckInput {
  product_name: string;
  product_version: string;
  source_ref?: string;
  description?: string;
  markets: string[];
  jurisdictions: string[];
  page_size: number;
  previous_check_id?: number | null;
  client_token: string;
  image: File;
}

export interface ReviewInput {
  client_token: string;
  market: string;
  risk_level: RiskLevel;
  decision: Decision;
  basis: string;
  uncovered_scope: string;
  conditions?: string | null;
  evidence_candidate_ids: number[];
  review_date?: string | null;
}

/** 防重复提交的客户端令牌：同一次操作的重试/双击复用同一个。 */
export function newToken(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, '');
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export async function getRiskMeta() {
  return request<RiskMeta>('/api/risk/meta', { method: 'GET' });
}

export async function createCheck(input: CreateCheckInput) {
  const fd = new FormData();
  fd.append('product_name', input.product_name);
  fd.append('product_version', input.product_version);
  if (input.source_ref) fd.append('source_ref', input.source_ref);
  if (input.description) fd.append('description', input.description);
  fd.append('markets', input.markets.join(','));
  fd.append('jurisdictions', input.jurisdictions.join(','));
  fd.append('page_size', String(input.page_size));
  if (input.previous_check_id) fd.append('previous_check_id', String(input.previous_check_id));
  fd.append('client_token', input.client_token);
  fd.append('image', input.image);
  return request<{ id: number; search_status: TaskStatus }>('/api/risk/checks', {
    method: 'POST',
    data: fd,
  });
}

export async function listChecks(params: {
  page: number;
  page_size: number;
  search_status?: TaskStatus;
  review_state?: 'none' | 'partial' | 'complete';
}) {
  return request<{ total: number; page: number; page_size: number; items: CheckSummary[] }>(
    '/api/risk/checks',
    { method: 'GET', params },
  );
}

export async function getCheck(id: number) {
  return request<CheckDetail>(`/api/risk/checks/${id}`, { method: 'GET' });
}

/** 原图要带登录凭证读取，不能直接作为 <img src>；取回 Blob 再转对象 URL。 */
export async function getCheckImage(id: number) {
  return request<Blob>(`/api/risk/checks/${id}/image`, { method: 'GET', responseType: 'blob' });
}

/** 已存档的附图或公报：带登录凭证取 Blob，再转对象 URL 展示。 */
export async function getEvidenceFile(checkId: number, candidateId: number, kind: FileDetailKind, seq: number) {
  return request<Blob>(`/api/risk/checks/${checkId}/files/${candidateId}/${kind}/${seq}`, {
    method: 'GET',
    responseType: 'blob',
  });
}

export async function requestNextPage(id: number, client_token: string) {
  return request<TaskAccepted>(`/api/risk/checks/${id}/pages`, { method: 'POST', data: { client_token } });
}

export async function requestRetry(id: number, client_token: string) {
  return request<TaskAccepted>(`/api/risk/checks/${id}/retry`, { method: 'POST', data: { client_token } });
}

export async function requestDetails(
  id: number,
  body: { client_token: string; candidate_ids: number[]; kinds?: DetailKind[] },
) {
  return request<TaskAccepted>(`/api/risk/checks/${id}/evidence`, { method: 'POST', data: body });
}

export async function addReview(id: number, body: ReviewInput) {
  return request<Review>(`/api/risk/checks/${id}/reviews`, { method: 'POST', data: body });
}
