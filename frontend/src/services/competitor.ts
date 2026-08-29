import { request } from '@umijs/max';

/**
 * 竞对监控（关注清单，P0）。issue #56 的数据获取层（浏览器插件/Keepa）与「今日上新」
 * 页不在本变更范围——这里只有分组、关注关系、预览/确认两步导入。
 *
 * 与我的词库（`services/library.ts`）的关键差异：**分组不绑定站点**（同一分组可以混
 * US/GB/DE），所以没有 `site` 选择器驱动查询；主表格改由 `group_id`（可空，缺省查全部
 * 关注）驱动。
 */

export type WatchStatus = 'PENDING_BASELINE' | 'MONITORING' | 'PAUSED';

export interface CompetitorGroup {
  group_id: number;
  name: string;
  watch_count: number;
  created_at: string;
  updated_at: string;
}

export interface WatchRow {
  watch_id: number;
  seller_id: string;
  site: string;
  group_id: number | null;
  group_name: string | null;
  priority: number;
  note: string | null;
  status: WatchStatus;
  created_at: string;
}

export interface WatchListBody {
  group_id?: number | null;
  page?: number;
  perPage?: number;
}

export interface WatchListResponse {
  success: boolean;
  data: WatchRow[];
  total: number;
  /** 当前用户的关注总数，不受 group_id 过滤影响——配额条读这个,不是 total */
  watch_count: number;
  max_watch: number;
}

export interface UnparsableEntry {
  text: string;
  reason: string;
}

export interface PreviewSample {
  seller_id: string;
  site: string;
  is_new: boolean;
  group_name: string | null;
}

export interface PreviewResponse {
  preview_token: string;
  expires_at: string;
  parsed: number;
  new: number;
  existing: number;
  unparsable: UnparsableEntry[];
  /** 书签文件夹路径拍平出的候选分组名,纯文本粘贴时恒为空 */
  groups: string[];
  samples: PreviewSample[];
}

export type CommitMode = 'append' | 'replace_group';

export interface CommitBody {
  preview_token: string;
  mode: CommitMode;
  target_group_id?: number | null;
}

export interface CommitResult {
  created_sellers: number;
  created_watches: number;
  updated_watches: number;
  removed_watches: number;
  watch_count: number;
}

export async function listGroups() {
  return request<CompetitorGroup[]>('/api/competitor/groups', { method: 'GET' });
}

export async function createGroup(name: string) {
  return request<CompetitorGroup>('/api/competitor/groups', {
    method: 'POST',
    data: { name },
  });
}

export async function renameGroup(groupId: number, name: string) {
  return request<CompetitorGroup>(`/api/competitor/groups/${groupId}`, {
    method: 'PATCH',
    data: { name },
  });
}

export async function deleteGroup(groupId: number) {
  return request<{ success: boolean }>(`/api/competitor/groups/${groupId}`, {
    method: 'DELETE',
  });
}

export async function listWatches(body: WatchListBody) {
  return request<WatchListResponse>('/api/competitor/watches', {
    method: 'POST',
    data: body,
  });
}

export async function updateWatch(watchId: number, patch: { group_id?: number | null; note?: string | null }) {
  return request<{ success: boolean }>(`/api/competitor/watches/${watchId}`, {
    method: 'PATCH',
    data: patch,
  });
}

export async function removeWatches(watchIds: number[]) {
  return request<{ success: boolean; removed: number; watch_count: number }>(
    '/api/competitor/watches',
    { method: 'DELETE', data: { watch_ids: watchIds } },
  );
}

/**
 * 预览：粘贴文本与文件二选一,由调用方保证。`fallbackSite` 只在裸 ID 或站点信息缺失的
 * 链接上生效,后端不会用它覆盖链接自带的站点。
 *
 * 走 `multipart/form-data`——后端 `/watch/preview` 用 `Form`/`File` 接收,不是 JSON
 * body（Pydantic 模型不能直接承载 UploadFile 与普通字段的混合表单)。
 */
export async function previewImport(input: {
  rawText?: string;
  file?: File;
  fallbackSite?: string;
}) {
  const formData = new FormData();
  if (input.rawText) formData.append('raw_text', input.rawText);
  if (input.file) formData.append('file', input.file);
  if (input.fallbackSite) formData.append('fallback_site', input.fallbackSite);
  return request<PreviewResponse>('/api/competitor/watch/preview', {
    method: 'POST',
    data: formData,
  });
}

export async function commitImport(body: CommitBody) {
  return request<CommitResult>('/api/competitor/watch/commit', {
    method: 'POST',
    data: body,
  });
}
