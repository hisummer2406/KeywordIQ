import { request } from '@umijs/max';
import type { View } from './analysis';

/** 关键词排名趋势详情（issue #86）。市场分析与我的词库共用同一个只读端点。 */

export type TrendRange = 'RECENT_30D' | 'ALL';

export type TrendPointStatus = 'RANKED' | 'DROPPED' | 'NO_DATA' | 'NOT_IMPORTED' | 'OUT_OF_RETENTION';

export interface KeywordTrendPoint {
  date: string;
  status: TrendPointStatus;
  /** 只有 RANKED 才有值。DROPPED 的"最后在榜排名"不进这个字段——那是另一天的数据。 */
  ranking: number | null;
}

export interface KeywordTrendStat {
  date: string;
  ranking: number;
}

export interface KeywordTrendCoverage {
  start: string;
  end: string;
}

export interface KeywordTrendResponse {
  success: boolean;
  keyword_id: number;
  keyword: string;
  site: string;
  view: View;
  range: TrendRange;
  /** 实际使用的锚定期。为 null 仅当该 (site,view) 完全没有任何数据。 */
  on: string | null;
  coverage: KeywordTrendCoverage | null;
  points: KeywordTrendPoint[];
  best_ranking: KeywordTrendStat | null;
  latest_available: KeywordTrendStat | null;
  /** 三项首次证据（#178），取自关键词维度，不随视图变；旧后端没有该字段 */
  first_evidence?: KeywordFirstEvidence | null;
}

/** 一项首次证据。basis：IMPORTED 确认首次 / EARLIEST_OBSERVED 最早可观测 / UNKNOWN 未知 / ABSENT 未上过该榜 */
export interface FirstEvidence {
  date: string | null;
  basis: string;
  /** 仅共享监测首次：日期来自日报还是周报 */
  period_type?: View | null;
}

export interface KeywordFirstEvidence {
  day: FirstEvidence;
  week: FirstEvidence;
  monitor: FirstEvidence;
}

export interface KeywordTrendQuery {
  keywordId: number;
  site: string;
  view: View;
  range: TrendRange;
  /**
   * 来源锚定期，保留打开详情时的日期语境（不静默跳到今天）。
   * 日/周切换时同样传这个值——后端按"取 <= 该日期的最新一期"映射到目标视图。
   */
  on?: string;
}

export async function getKeywordTrend(query: KeywordTrendQuery) {
  return request<KeywordTrendResponse>('/api/keyword/trend', {
    method: 'GET',
    params: {
      keyword_id: query.keywordId,
      site: query.site,
      view: query.view,
      range: query.range,
      ...(query.on ? { on: query.on } : {}),
    },
  });
}
