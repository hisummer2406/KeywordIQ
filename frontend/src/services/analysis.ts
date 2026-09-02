import { history, request } from '@umijs/max';
import { clearToken, getToken } from '@/utils/token';

/** 站点作用域分析查询。列表 POST /search 返 ProTable 契约 + 作用域回显。 */

export type View = 'DAY' | 'WEEK';

/** 行集合维度。**不是筛选条件**——筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行。 */
export type Include = 'ALL' | 'LISTED' | 'DROPPED';

export interface AnalysisRow {
  keyword_id: number;
  keyword: string;
  /**
   * 掉榜行（`is_dropped`）的当期字段一律为空：当期排名、商品槽位、品牌、类目、份额。
   * **不用最后在榜期的值顶替**——顶替会让用户读到一个看似当期、实则属于另一期的数字。
   * 它能提供的只有 `last_seen_date` / `last_ranking` 与历史排名、趋势。
   */
  is_dropped: boolean;
  last_seen_date: string | null;
  last_ranking: number | null;
  current_ranking: number | null;
  previous_ranking: number | null;
  ranking_change: number | null;
  is_new: boolean;
  /**
   * 三个历史参照点的排名，顺序固定（日视图：昨天 / 3 天前 / 7 天前）。
   * 参照期缺失（未导入或已被保留清理移出）时该位为 `null`，不补齐、不用相邻期顶替。
   */
  history: (number | null)[];
  /**
   * 三个商品槽位与点击量名次一一对应。槽位缺失时该组字段为空（ASIN/标题/品牌/类目为
   * null，两个份额为 0），**不由后一名顶替**——名次与槽位的对应关系是这些字段的意义所在。
   *
   * 注意：这三组只是展示数据。品牌/类目/ASIN/标题/份额的筛选与排序仍只作用于 top1。
   */
  top1_asin: string | null;
  top1_title: string | null;
  top1_brand: string | null;
  top1_category: string | null;
  top1_click_share: number;
  top1_conversion_share: number;
  top2_asin: string | null;
  top2_title: string | null;
  top2_brand: string | null;
  top2_category: string | null;
  top2_click_share: number;
  top2_conversion_share: number;
  top3_asin: string | null;
  top3_title: string | null;
  top3_brand: string | null;
  top3_category: string | null;
  top3_click_share: number;
  top3_conversion_share: number;
  trend?: number[];
  trend_dates?: string[];
}

export interface SearchBody {
  site?: string;
  view: View;
  date?: string;
  page?: number;
  perPage?: number;
  /**
   * 是否要后端算总数。总数是精确计数，代价与命中行数同阶（周报最坏约 0.9s），
   * 而同一组条件下翻页/改排序不会改变它——只在条件变化时传 true。
   */
  include_total?: boolean;
  orderBy?: string;
  orderDir?: 'asc' | 'desc';
  keyword?: string;
  brands?: string[];
  /** 类目精确匹配集合，命中其一即可；空集合不传（视同不筛） */
  categories?: string[];
  asin?: string;
  product_title?: string;
  ranking_min?: number;
  ranking_max?: number;
  change_min?: number;
  change_max?: number;
  click_share_min?: number;
  click_share_max?: number;
  conversion_share_min?: number;
  conversion_share_max?: number;
  is_new?: boolean;
  /**
   * 竞对新发现：前三商品槽位任一 ASIN，是当前用户关注的卖家**近 7 日新发现**的 ASIN
   * （晚于该卖家基线批次且落在窗口内），与 `is_new` 同构（不传 = 不筛）。
   *
   * **不是"关注卖家的全部在售商品"**——空结果有两个成因：插件未上线（数据没进来）、
   * 或商品都是 7 天前见到的（过了窗口）。界面提示两条都要说（见 `COMPETITOR_NEW_HINT`）。
   * 字段名沿用 `competitor_new`，界面标签是「竞对新发现」：measure 读作 new *discovery*，
   * 被动采集证明不了商品何时上架。
   */
  competitor_new?: boolean;
  /** 行集合维度，缺省 ALL。切到 DROPPED 时其余筛选在界面上必须置灰——它们会让掉榜段恒为空。 */
  include?: Include;
}

export interface SearchResponse {
  success: boolean;
  data: AnalysisRow[];
  /** `null` = 本次没算（请求带了 include_total:false），调用方沿用上一次的值；`0` = 确实没有命中 */
  total: number | null;
  site: string;
  view: View;
  date: string | null;
  /** 该切片的品牌/类目尚未从 OSS 重导补齐 */
  pending_rebuild?: boolean;
  message?: string;
}

export interface ScopeInfo {
  site: string;
  view: View;
  latest_date: string | null;
  available_dates: string[];
}

export interface BrandOption {
  brand: string;
  count: number;
}

export interface CategoryOption {
  category: string;
  count: number;
}

export async function searchAnalysis(body: SearchBody) {
  return request<SearchResponse>('/api/analysis/search', { method: 'POST', data: body });
}

/**
 * 作用域元数据（可用日期与最新一期）。
 *
 * `skipErrorHandler` 供**调用方自己兜底**的场景使用：首屏拿用户保存的站点乐观发一次，
 * 站点若已从启用清单移除，后端报错（非法站点不回退是既有约定），前端捕获后回退到默认
 * 站点。那种情况下全局错误提示必须闭嘴——否则用户会同时看到一个红色报错和一句
 * 「已切回默认站点」，自相矛盾。**跳过之后调用方必须自行呈现**。
 */
export async function getScope(
  site: string | undefined,
  view: View,
  opts?: { skipErrorHandler?: boolean },
) {
  return request<ScopeInfo>('/api/analysis/scope', {
    method: 'GET',
    params: { view, ...(site ? { site } : {}) },
    skipErrorHandler: opts?.skipErrorHandler,
  });
}

/**
 * 类目下拉选项。
 *
 * **日期是选项口径的一部分**：统计按 (站点, 周期, 日期) 分片，与筛选严格同粒度。
 * 不传日期时后端取最新可用日期——那是给首屏用的，不是"不限日期"。
 */
export async function getCategories(site: string | undefined, view: View, date?: string | null) {
  return request<CategoryOption[]>('/api/analysis/categories', {
    method: 'GET',
    params: { view, ...(site ? { site } : {}), ...(date ? { date } : {}) },
  });
}

/** Content-Disposition 里的文件名，取不到就交给调用方兜底。 */
function filenameFrom(disposition: string | null): string | undefined {
  return disposition?.match(/filename="?([^";]+)"?/)?.[1];
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * 导出当前作用域与筛选下的 CSV（design 决策 7 的中小导出路径）。
 *
 * 用裸 fetch 而非 umi request：需要拿到 Blob 与响应头里的文件名，且要按 FastAPI 的
 * {detail} 形状把 413（超出行数上限）原文透给用户。代价是绕过了 app.tsx 的 errorConfig，
 * 故 401 的清 token / 跳登录在这里自行处理，保持与全局一致。
 */
export async function exportAnalysis(body: SearchBody): Promise<void> {
  const base = process.env.UMI_APP_API_BASE || '';
  const token = getToken();
  const res = await fetch(`${base}/api/analysis/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    if (res.status === 401) {
      clearToken();
      history.push('/login');
      throw new Error('登录已过期，请重新登录');
    }
    let detail = `导出失败（${res.status}）`;
    try {
      // 统一错误契约 {success,code,message,request_id}；此前读的是 FastAPI 的 detail
      detail = (await res.json())?.message || detail;
    } catch {
      // 非 JSON 错误体（如网关返回的 HTML），用兜底文案
    }
    throw new Error(detail);
  }

  triggerDownload(
    await res.blob(),
    filenameFrom(res.headers.get('Content-Disposition')) ?? 'export.csv',
  );
}

/** 品牌下拉选项。与 getCategories 同构，作用域参数一致（含日期）。 */
export async function getBrands(site: string | undefined, view: View, date?: string | null) {
  return request<BrandOption[]>('/api/analysis/brands', {
    method: 'GET',
    params: { view, ...(site ? { site } : {}), ...(date ? { date } : {}) },
  });
}
