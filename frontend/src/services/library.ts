import { request } from '@umijs/max';
import type { AnalysisRow, View } from './analysis';

/**
 * 工具箱 · 我的词库。
 *
 * **分组绑定站点**：站点在建组时定死，之后所有请求的站点都由 group_id 决定——盯盘屏
 * 因此没有站点选择器，否则会出现「选了 DE 却在看 US 分组」这种自相矛盾的状态。
 *
 * 盯盘行是分析屏行的超集（`AnalysisRow` + status + added_at），因为两屏读的是同一套
 * 口径、共用同一份列渲染。
 */

/** 盯盘行的三种状态。行集合由清单决定，掉榜与无数据的词同样占一行。 */
export type LibraryRowStatus = 'LISTED' | 'DROPPED' | 'MISSING';

export interface LibraryWatchRow extends AnalysisRow {
  status: LibraryRowStatus;
  /** 加入词库的时间 */
  added_at: string;
}

export interface KeywordGroup {
  group_id: number;
  site: string;
  name: string;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export type LibrarySortField =
  | 'current_ranking'
  | 'ranking_change'
  | 'previous_ranking'
  | 'top1_click_share'
  | 'top1_conversion_share'
  | 'added_at';

export interface WatchBody {
  group_id: number;
  view?: View;
  date?: string | null;
  page?: number;
  perPage?: number;
  orderBy?: LibrarySortField;
  orderDir?: 'asc' | 'desc';
}

export interface WatchResponse {
  success: boolean;
  data: LibraryWatchRow[];
  /** 分组条目数——不是命中事实表的行数 */
  total: number;
  group_id: number;
  site: string;
  view: View;
  date: string | null;
}

export interface LibraryScopeInfo {
  site: string;
  view: View;
  dates: string[];
}

/** 加词结果。新增与已存在分开回传：只说「成功」会让用户以为都是新加的。 */
export interface ItemsAddResult {
  added: number;
  existed: number;
  item_count: number;
}

export async function listGroups(site?: string) {
  return request<KeywordGroup[]>('/api/toolbox/library/groups', {
    method: 'GET',
    params: site ? { site } : {},
  });
}

export async function createGroup(site: string, name: string) {
  return request<KeywordGroup>('/api/toolbox/library/groups', {
    method: 'POST',
    data: { site, name },
  });
}

export async function renameGroup(groupId: number, name: string) {
  return request<KeywordGroup>(`/api/toolbox/library/groups/${groupId}`, {
    method: 'PATCH',
    data: { name },
  });
}

export async function deleteGroup(groupId: number) {
  return request<{ success: boolean }>(`/api/toolbox/library/groups/${groupId}`, {
    method: 'DELETE',
  });
}

export async function addItems(groupId: number, keywordIds: number[]) {
  return request<ItemsAddResult>(`/api/toolbox/library/groups/${groupId}/items`, {
    method: 'POST',
    data: { keyword_ids: keywordIds },
  });
}

export async function removeItems(groupId: number, keywordIds: number[]) {
  return request<{ removed: number; item_count: number }>(
    `/api/toolbox/library/groups/${groupId}/items`,
    { method: 'DELETE', data: { keyword_ids: keywordIds } },
  );
}

export async function getLibraryScope(groupId: number, view: View) {
  return request<LibraryScopeInfo>(`/api/toolbox/library/groups/${groupId}/scope`, {
    method: 'GET',
    params: { view },
  });
}

export async function watchGroup(body: WatchBody) {
  return request<WatchResponse>('/api/toolbox/library/watch', {
    method: 'POST',
    data: body,
  });
}
