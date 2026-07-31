import { history, request } from '@umijs/max';
import { clearToken, getToken } from '@/utils/token';

/** 站点作用域分析查询。列表 POST /search 返 ProTable 契约 + 作用域回显。 */

export type View = 'DAY' | 'WEEK';

export interface AnalysisRow {
  keyword_id: number;
  keyword: string;
  current_ranking: number;
  previous_ranking: number;
  ranking_change: number;
  is_new: boolean;
  top1_asin: string | null;
  top1_title: string | null;
  top1_brand: string | null;
  top1_category: string | null;
  top1_click_share: number;
  top1_conversion_share: number;
  trend?: number[];
  week_change?: number | null;
}

export interface SearchBody {
  site?: string;
  view: View;
  date?: string;
  page?: number;
  perPage?: number;
  orderBy?: string;
  orderDir?: 'asc' | 'desc';
  keyword?: string;
  brand?: string;
  category?: string;
  category_exact?: string;
  asin?: string;
  product_title?: string;
  ranking_min?: number;
  ranking_max?: number;
  is_new?: boolean;
}

export interface SearchResponse {
  success: boolean;
  data: AnalysisRow[];
  total: number;
  site: string;
  view: View;
  date: string | null;
  message?: string;
}

export interface ScopeInfo {
  site: string;
  view: View;
  latest_date: string | null;
  available_dates: string[];
}

export interface CategoryOption {
  category: string;
  count: number;
}

export async function searchAnalysis(body: SearchBody) {
  return request<SearchResponse>('/api/analysis/search', { method: 'POST', data: body });
}

export async function getScope(site: string | undefined, view: View) {
  return request<ScopeInfo>('/api/analysis/scope', {
    method: 'GET',
    params: { view, ...(site ? { site } : {}) },
  });
}

export async function getCategories(site: string | undefined, view: View) {
  return request<CategoryOption[]>('/api/analysis/categories', {
    method: 'GET',
    params: { view, ...(site ? { site } : {}) },
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
      detail = (await res.json())?.detail || detail;
    } catch {
      // 非 JSON 错误体（如网关返回的 HTML），用兜底文案
    }
    throw new Error(detail);
  }

  triggerDownload(await res.blob(), filenameFrom(res.headers.get('Content-Disposition')) ?? 'export.csv');
}
