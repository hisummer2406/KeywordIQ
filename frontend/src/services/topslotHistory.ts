import { request } from '@umijs/max';
import type { View } from './analysis';

/**
 * ASIN/品牌悬浮历史（issue #96）。给定当前行的 keyword_id 与一个身份（ASIN 原文或
 * 品牌原文），返回该身份在这个关键词下、保留窗口到来源锚定日之间实际进入点击前三的
 * 期次列表。
 */

export type TopslotKind = 'ASIN' | 'BRAND';

export interface TopslotHistoryItem {
  date: string;
  /** 当期最佳槽位名次：1/2/3。 */
  rank: number;
}

export interface TopslotHistoryResponse {
  success: boolean;
  /** 完整匹配范围内的精确期数，与本页 items 条数无关。 */
  total: number;
  items: TopslotHistoryItem[];
}

export interface TopslotHistoryQuery {
  site: string;
  view: View;
  keywordId: number;
  kind: TopslotKind;
  value: string;
  /** 来源页面已生效查询的锚定日，必填、不做退化——不是"今天"也不是"最新一期"。 */
  on: string;
  page: number;
  pageSize: number;
}

export async function getTopslotHistory(query: TopslotHistoryQuery) {
  return request<TopslotHistoryResponse>('/api/analysis/topslot-history', {
    method: 'GET',
    params: {
      site: query.site,
      view: query.view,
      keyword_id: query.keywordId,
      kind: query.kind,
      value: query.value,
      on: query.on,
      page: query.page,
      page_size: query.pageSize,
    },
  });
}
