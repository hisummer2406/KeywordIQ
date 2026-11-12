import { request } from '@umijs/max';
import type { DossierStatus } from '@/services/opportunityDossier';

/**
 * 机会中心线索——信号洞察专家（change add-opportunity-signal-leads）。
 *
 * 线索是持续增长榜的只读视图：榜单在导入时算定，这里只多带回「这个词已经在哪些未归档的档案里」。
 * 判定口径随响应下发（criteria），界面不硬编码。
 */

export interface GrowthLead {
  keyword_id: number;
  keyword: string;
  start_ranking: number;
  end_ranking: number;
  /** 累计净增名次，正数为名次前进 */
  rank_gain: number;
  /** 窗口内每日环比的最小值，0~1 的小数 */
  min_growth_rate: number;
  trend: number[];
  trend_dates: string[];
  dossiers: { id: number; title: string; status: DossierStatus }[];
}

export interface GrowthLeadsResult {
  site: string;
  date: string | null;
  total: number;
  page: number;
  page_size: number;
  items: GrowthLead[];
  /** 窗口内未导入的期次；非空时榜单可能据实为空 */
  missing_dates: string[];
  available_dates: string[];
  criteria: { window_days: number; min_rank_gain: number; min_daily_rate: number };
}

export async function listGrowthLeads(params: { site: string; date?: string; page: number; page_size: number }) {
  return request<GrowthLeadsResult>('/api/opportunity/leads/growth', { method: 'GET', params });
}
