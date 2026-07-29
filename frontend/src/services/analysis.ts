import { request } from '@umijs/max';

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
