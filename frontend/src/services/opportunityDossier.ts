import { request } from '@umijs/max';

/**
 * 机会档案（change add-opportunity-dossier）。
 *
 * 档案团队可见：持有 opportunity:view 即可读写，所有写入由后端署名。只有「追问」会产生外部调用
 * （大模型）——它只写队列、返回 202，由后台执行；其余接口只读写库。
 */

export type DossierStatus = 'researching' | 'advance' | 'need_evidence' | 'watch' | 'reject';
export type Verdict = Exclude<DossierStatus, 'researching'>;
export type GapCategory = 'search_volume' | 'sales' | 'price' | 'bsr' | 'reviews' | 'supply_cost' | 'trademark';
export type ManualCategory = GapCategory | 'other';
/** 逐期状态：R 在榜 / D 掉榜 / N 已导入未收录 / X 未导入 / O 超出保留期 */
export type PointStatus = 'R' | 'D' | 'N' | 'X' | 'O';
/** [日期, 状态, 排名（仅在榜时有值）] */
export type SeriesPoint = [string, PointStatus, number | null];

export interface DossierSummary {
  id: number;
  title: string;
  idea: string | null;
  site: string;
  site_name: string;
  /** Amazon 域名（来自站点注册表），拼商品链接用 */
  site_domain: string | null;
  status: DossierStatus;
  recheck_date: string | null;
  archived_at: string | null;
  creator_id: number;
  creator_name: string | null;
  assignee_id: number;
  assignee_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface DossierListItem extends DossierSummary {
  keyword_count: number;
}

export interface RankStat {
  date: string;
  ranking: number;
}

export interface Series {
  anchor: string | null;
  points: SeriesPoint[];
  ranked_periods: number;
  imported_periods: number;
  best: RankStat | null;
  latest: RankStat | null;
  /** 仅日榜：最新在榜期 7 天前当日的排名；当日不在榜即为空 */
  rank_7d_ago?: RankStat | null;
}

export interface TopProduct {
  slot: number;
  asin: string;
  title: string | null;
  click_share: number | null;
  conversion_share: number | null;
}

export interface FirstEvidenceItem {
  date: string | null;
  basis: string;
  period_type?: 'DAY' | 'WEEK' | null;
}

export interface KeywordEvidence {
  keyword: string;
  keyword_id: number | null;
  resolved: boolean;
  day?: Series;
  week?: Series;
  first_evidence?: { day: FirstEvidenceItem; week: FirstEvidenceItem; monitor: FirstEvidenceItem } | null;
  top3?: {
    date: string;
    ranking: number;
    products: TopProduct[];
    click_share_sum: number | null;
    /** 关键词级的前三品牌与类目——与商品槽位无关，不要一一对应到商品上 */
    brands: string[];
    categories: string[];
  } | null;
  top1_churn?: {
    window_start: string;
    window_end: string;
    ranked_periods: number;
    distinct_top1: number;
    series: [string, string | null][];
  } | null;
  /** 在榜期数不足，不判断趋势 */
  thin?: boolean;
}

export interface SnapshotPayload {
  version: number;
  site: string;
  anchor_day: string | null;
  anchor_week: string | null;
  keywords: KeywordEvidence[];
  shared_asins: { asin: string; title: string | null; keywords: string[] }[];
}

export interface Snapshot {
  id: number;
  anchor_day: string | null;
  anchor_week: string | null;
  created_by: number;
  created_by_name: string | null;
  created_at: string;
  payload: SnapshotPayload;
}

export interface ChangeSide {
  resolved: boolean;
  latest: RankStat | null;
  ranked_periods: number | null;
  imported_periods?: number;
  top1_asin: string | null;
}

export interface ChangeRow {
  keyword: string;
  state: 'kept' | 'added' | 'removed';
  before: ChangeSide | null;
  after: ChangeSide | null;
  top1_changed?: boolean;
}

export interface Changes {
  basis: 'decision' | 'previous';
  base_snapshot_id: number;
  base_created_at: string;
  base_anchor_day: string | null;
  anchor_day: string | null;
  periods_unchanged: boolean;
  keywords: ChangeRow[];
}

export interface ManualEvidence {
  id: number;
  category: ManualCategory;
  content: string;
  source_name: string;
  source_url: string | null;
  observed_date: string;
  created_by: number;
  created_by_name: string | null;
  created_at: string;
  voided_at: string | null;
  voided_by_name: string | null;
}

export interface IpReviewBrief {
  market: string;
  risk_level: string;
  decision: string;
  signer_name?: string | null;
  created_at?: string;
}

export interface IpResearchBrief {
  id: number;
  product_name: string;
  owner_id: number;
  owner_name: string | null;
  markets: string[];
  candidate_count: number;
  last_task: { kind: string; status: string } | null;
  reviews: IpReviewBrief[];
  created_at: string;
}

export interface IpSummary {
  /** unchecked = 未核查（没有关联调研，或都没有人工意见）——不是低风险 */
  state: 'unchecked' | 'reviewed';
  researches: IpResearchBrief[];
}

export interface Decision {
  id: number;
  verdict: Verdict;
  reason: string;
  next_step: string | null;
  recheck_date: string | null;
  snapshot_id: number | null;
  ip_status: { state: 'unchecked' | 'reviewed'; research_count: number; reviews: { market: string; risk_level: string; decision: string }[] };
  signer_id: number;
  signer_name: string | null;
  created_at: string;
}

export interface DossierMessage {
  id: number;
  question: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'unknown';
  error_category: string | null;
  answer: string | null;
  cited_evidence_ids: number[];
  model: string | null;
  asker_id: number;
  asker_name: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface DossierDetail {
  dossier: DossierSummary;
  today: string;
  keywords: { id: number; keyword: string; keyword_id: number | null; resolved: boolean }[];
  snapshot: Snapshot | null;
  snapshot_count: number;
  snapshot_stale: { new_data: boolean; keywords_changed: boolean } | null;
  latest_anchor_day: string | null;
  latest_anchor_week: string | null;
  changes: Changes | null;
  gaps: { category: GapCategory; connected: false; evidence: ManualEvidence[] }[];
  other_evidence: ManualEvidence[];
  decisions: Decision[];
  ip: IpSummary;
  messages: DossierMessage[];
  asking: boolean;
  llm_configured: boolean;
}

export interface DossierListResult {
  total: number;
  page: number;
  page_size: number;
  due_count: number;
  today: string;
  items: DossierListItem[];
}

const BASE = '/api/opportunity/dossiers';

export async function listDossiers(params: {
  page: number;
  page_size: number;
  status?: DossierStatus;
  due?: boolean;
  mine?: boolean;
  archived?: boolean;
  q?: string;
  /** 只看某个站点的档案（服务端在分页前筛选） */
  site?: string;
}) {
  return request<DossierListResult>(BASE, { method: 'GET', params });
}

export async function createDossier(body: {
  title: string;
  idea?: string | null;
  site?: string | null;
  keywords: string[];
  keyword_group_id?: number | null;
  client_token: string;
}) {
  return request<{ id: number }>(BASE, { method: 'POST', data: body });
}

export async function getDossier(id: number) {
  return request<DossierDetail>(`${BASE}/${id}`, { method: 'GET' });
}

export async function updateDossier(
  id: number,
  body: { title?: string; idea?: string; archived?: boolean; take_over?: boolean },
) {
  return request<{ id: number }>(`${BASE}/${id}`, { method: 'PATCH', data: body });
}

export async function addDossierKeywords(id: number, keywords: string[]) {
  return request<{ added: number; unresolved: string[] }>(`${BASE}/${id}/keywords`, { method: 'POST', data: { keywords } });
}

export async function removeDossierKeyword(id: number, keywordRowId: number) {
  return request<void>(`${BASE}/${id}/keywords/${keywordRowId}`, { method: 'DELETE' });
}

export async function refreshDossierSnapshot(id: number) {
  return request<{ snapshot_id: number; created: boolean }>(`${BASE}/${id}/snapshots`, { method: 'POST' });
}

export async function addManualEvidence(
  id: number,
  body: { category: ManualCategory; content: string; source_name: string; source_url?: string | null; observed_date: string },
) {
  return request<{ id: number }>(`${BASE}/${id}/evidence`, { method: 'POST', data: body });
}

export async function voidManualEvidence(id: number, evidenceId: number) {
  return request<{ id: number }>(`${BASE}/${id}/evidence/${evidenceId}/void`, { method: 'POST' });
}

export async function addDossierDecision(
  id: number,
  body: { verdict: Verdict; reason: string; next_step?: string | null; recheck_date?: string | null; client_token: string },
) {
  return request<{ id: number }>(`${BASE}/${id}/decisions`, { method: 'POST', data: body });
}

export async function listLinkableResearches() {
  return request<{ items: { id: number; product_name: string; dossier_id: number | null; created_at: string }[] }>(
    `${BASE}/linkable-researches`,
    { method: 'GET' },
  );
}

export async function linkDossierResearch(id: number, researchId: number) {
  return request<{ research_id: number; dossier_id: number }>(`${BASE}/${id}/ip-researches/${researchId}`, { method: 'PUT' });
}

export async function unlinkDossierResearch(id: number, researchId: number) {
  return request<void>(`${BASE}/${id}/ip-researches/${researchId}`, { method: 'DELETE' });
}

export async function askDossier(id: number, question: string) {
  return request<{ id: number; status: string }>(`${BASE}/${id}/messages`, { method: 'POST', data: { question } });
}
