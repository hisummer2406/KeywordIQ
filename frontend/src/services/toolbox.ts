import { request } from '@umijs/max';

/**
 * 工具箱 · 持续增长市场。
 *
 * 作用域只有「站点 + 锚定日」两维，**没有视图**——只有日报有「连续 7 天」这个概念。
 */

export interface GrowthRow {
  keyword_id: number;
  keyword: string;
  /** 窗口首期排名 */
  start_ranking: number;
  /** 锚定期排名 */
  end_ranking: number;
  /** 累计净增名次，正数为名次前进（排名越小越好） */
  rank_gain: number;
  /** 窗口内每日环比的最小值，**0~1 的小数**；换算成百分数只在展示层做一次 */
  min_growth_rate: number;
  /** 窗口内各期排名，按期次升序，点数恒等于 window_days */
  trend: number[];
  trend_dates: string[];
}

export type GrowthSortField = 'rank_gain' | 'min_growth_rate' | 'end_ranking';

export interface GrowthSearchBody {
  site?: string;
  date?: string;
  page?: number;
  perPage?: number;
  orderBy?: GrowthSortField;
  orderDir?: 'asc' | 'desc';
}

export interface GrowthSearchResponse {
  success: boolean;
  data: GrowthRow[];
  total: number;
  site: string;
  date: string | null;
  /**
   * 窗口内没有数据的期次。**非空时界面必须明示**：缺期会让榜单据实为空，而
   * 「今天没有增长词」与「有两天的数据没导进来」在界面上长得一模一样。
   */
  missing_dates: string[];
  window_days: number;
}

export interface GrowthScopeInfo {
  site: string;
  latest_date: string | null;
  available_dates: string[];
  /** 以下三项是判定口径，口径说明条据此拼文案——**不要在前端硬编码这些数值** */
  window_days: number;
  min_rank_gain: number;
  min_daily_rate: number;
}

export async function searchGrowth(body: GrowthSearchBody) {
  return request<GrowthSearchResponse>('/api/toolbox/growth/search', {
    method: 'POST',
    data: body,
  });
}

export async function getGrowthScope(site?: string) {
  return request<GrowthScopeInfo>('/api/toolbox/growth/scope', {
    method: 'GET',
    params: site ? { site } : {},
  });
}
