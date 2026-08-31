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

/* ------------------------------------------------------------------------ *
 * 独立站（Shopify）监控
 *
 * 与上面的 Amazon 卖家关注**共用分组**（`user_seller_group`）与权限点，但**不共用表
 * 与字段**：卖家由 `(seller_id, site)` 唯一确定，独立站由域名唯一确定；卖家侧有 BSR
 * 与评价数、没有上架时间，独立站侧有上架时间与变体、没有任何销量。
 * ------------------------------------------------------------------------ */

/**
 * 站点状态。**四种，绝不合并**——三类失败各有各的去处，而「从未拉取」是第四种：
 * 新加的站点还没到点，不是坏了。
 */
export type StoreStatus =
  | 'NEVER_FETCHED'
  | 'OK'
  | 'INCOMPLETE'
  | 'FAILED'
  | 'NO_CATALOG_API';

export interface StoreRow {
  domain: string;
  name: string | null;
  /** ISO 币种码。价格数字本身不带币种，界面必须靠它显示符号 */
  currency: string | null;
  country: string | null;
  group_id: number | null;
  group_name: string | null;
  note: string | null;
  watched_at: string;
  status: StoreStatus;
  catalog_complete: boolean;
  rank_ok: boolean;
  consecutive_failures: number;
  last_fetch_at: string | null;
  first_fetch_at: string | null;
  product_count: number;
  /** 近 7 日上架数，按数据源给的 published_at 算——首刷当天即准确 */
  new_in_7d: number;
}

export interface StoreListResponse {
  success: boolean;
  data: StoreRow[];
  total: number;
}

export async function listStores(body: {
  group_id?: number | null;
  ungrouped_only?: boolean;
  page?: number;
  perPage?: number;
}) {
  return request<StoreListResponse>('/api/competitor/stores/list', {
    method: 'POST',
    data: {
      group_id: body.group_id ?? null,
      ungrouped_only: body.ungrouped_only ?? false,
      page: body.page ?? 1,
      per_page: body.perPage ?? 20,
    },
  });
}

export async function addStore(body: {
  domain: string;
  group_id?: number | null;
  note?: string | null;
}) {
  return request<StoreRow>('/api/competitor/stores', { method: 'POST', data: body });
}

export async function updateStore(
  domain: string,
  body: { group_id?: number | null; note?: string | null; clear_group?: boolean },
) {
  return request<void>(`/api/competitor/stores/${encodeURIComponent(domain)}`, {
    method: 'PATCH',
    data: body,
  });
}

export async function removeStore(domain: string) {
  return request<void>(`/api/competitor/stores/${encodeURIComponent(domain)}`, {
    method: 'DELETE',
  });
}

export interface StoreProductRow {
  product_id: number;
  handle: string;
  title: string;
  vendor: string | null;
  product_type: string | null;
  image_url: string | null;
  published_at: string | null;
  first_seen_at: string;
  delisted_at: string | null;
  current_price: string | null;
  compare_at_price: string | null;
  available: boolean | null;
  /** 最近一次**店内**畅销名次。相对名次，不是销量，也不可跨店比较 */
  latest_rank: number | null;
}

export interface StoreProductListResponse {
  success: boolean;
  data: StoreProductRow[];
  total: number;
  currency: string | null;
}

export async function listStoreProducts(body: {
  domain: string;
  include?: 'LISTED' | 'DELISTED' | 'ALL';
  new_only?: boolean;
  page?: number;
  perPage?: number;
}) {
  return request<StoreProductListResponse>('/api/competitor/store-products', {
    method: 'POST',
    data: {
      domain: body.domain,
      include: body.include ?? 'LISTED',
      new_only: body.new_only ?? false,
      page: body.page ?? 1,
      per_page: body.perPage ?? 20,
    },
  });
}

/**
 * 上新流的一行。`time_kind` 不是可选的装饰：两类来源的时间语义不同——独立站是数据源
 * 给的上架时间（`PUBLISHED`），Amazon 侧只能是我们首次见到的时刻（`DISCOVERED`）。
 * 把后者显示成「上架于」，等于拿「我们发现得晚」冒充「他上得晚」。
 */
export interface NewArrivalRow {
  source: 'DTC' | 'AMAZON';
  source_key: string;
  source_name: string | null;
  product_key: string;
  title: string;
  image_url: string | null;
  at: string;
  time_kind: 'PUBLISHED' | 'DISCOVERED';
}

export interface NewArrivalResponse {
  success: boolean;
  data: NewArrivalRow[];
  total: number;
}

export async function listNewArrivals(body: {
  days?: number;
  page?: number;
  perPage?: number;
}) {
  return request<NewArrivalResponse>('/api/competitor/new-arrivals', {
    method: 'POST',
    data: { days: body.days ?? 7, page: body.page ?? 1, per_page: body.perPage ?? 20 },
  });
}

export interface PricePoint {
  observed_at: string;
  variant_id: number;
  sku: string | null;
  price: string | null;
  compare_at_price: string | null;
  available: boolean;
}

export interface RankPoint {
  captured_on: string;
  rank: number;
}

export interface ProductHistoryResponse {
  success: boolean;
  domain: string;
  product_id: number;
  currency: string | null;
  prices: PricePoint[];
  ranks: RankPoint[];
}

export async function getProductHistory(domain: string, productId: number) {
  return request<ProductHistoryResponse>(
    `/api/competitor/stores/${encodeURIComponent(domain)}/products/${productId}/history`,
    { method: 'GET' },
  );
}
