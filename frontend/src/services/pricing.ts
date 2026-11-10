import { request } from '@umijs/max';

/**
 * 定价核算（change add-pricing-calculation）。
 *
 * **金额一律是十进制字符串**（如 `"93.75"`）。前端只展示后端算好的结果，不用浮点数参与
 * 任何计算——核价的权威公式只在后端一份（design D9）。百分比输入在提交前用
 * {@link pctToRate} 做字符串移位，不经过 `Number`。
 */

export type PricingMode = 'target_profit' | 'target_margin' | 'fixed_price' | 'max_purchase';
export type Availability = 'available' | 'not_applicable' | 'needs_input' | 'missing_config';
export type SolveStatus = 'met' | 'not_met' | 'unsolvable' | 'not_computed';
export type RuleKind = 'fx_rate' | 'market_params' | 'referral_schedule' | 'fba_rate_card' | 'fbm_channel';
export type RuleStatus = 'draft' | 'active' | 'retired';

export interface MetaMarket {
  code: string;
  name: string;
  currency: string;
  /** 缺少的必需规则类别（空 = 已配齐） */
  missing: RuleKind[];
  categories: { key: string; name: string }[];
  fbm_channels: number;
  has_fba: boolean;
  price_includes_vat: boolean | null;
}

export interface PricingMeta {
  today: string;
  markets: MetaMarket[];
  attributes: { key: string; name: string }[];
  rule_kinds: { key: RuleKind; name: string }[];
  can_manage_rates: boolean;
}

export interface MarketInput {
  price?: string | null;
  duty_rate?: string | null;
  ads_rate?: string | null;
  return_rate?: string | null;
  payout_rate?: string | null;
  storage_months?: string | null;
  headhaul_per_unit?: string | null;
  fba_fee_override?: string | null;
  placement_option?: string | null;
}

export interface CompetitorPriceInput {
  market: string;
  price: string;
  currency: string;
  includes_vat: boolean;
  note?: string;
}

export interface PricingInput {
  mode: PricingMode;
  markets: string[];
  purchase_cost?: string | null;
  packaging_labor?: string;
  weight_kg?: string | null;
  length_cm?: string | null;
  width_cm?: string | null;
  height_cm?: string | null;
  attributes?: string[];
  liquid_ml?: string | null;
  bottles?: number;
  category?: string | null;
  target_profit?: string | null;
  target_margin?: string | null;
  declared_basis?: 'purchase_cost' | 'fixed';
  declared_value?: string | null;
  include_fba?: boolean;
  include_fbm?: boolean;
  market_inputs?: Record<string, MarketInput>;
  price_overrides?: Record<string, string>;
  competitor_prices?: CompetitorPriceInput[];
}

export interface RuleRef {
  kind: RuleKind;
  rule_id: number;
  version: number;
  scope_key: string;
  source: string;
}

export interface FeeLine {
  key: string;
  label: string;
  amount_cny: string;
  amount_orig: string | null;
  currency: string;
  basis: string;
  rule: RuleRef | null;
}

export interface Evaluation {
  price: string;
  revenue_cny: string;
  net_revenue_cny: string;
  lines: FeeLine[];
  profit_cny: string;
  margin: string | null;
  roi: string | null;
}

export interface OptionResult {
  market: string;
  option_key: string;
  label: string;
  availability: Availability;
  reasons: string[];
  solve_status: SolveStatus;
  evaluation: Evaluation | null;
  target_price: string | null;
  breakeven_price: string | null;
  max_purchase_cost: string | null;
  target_gap_cny: string | null;
  adjusted: Evaluation | null;
  adjusted_met: boolean | null;
  adjusted_gap_cny: string | null;
  recommended: boolean;
  rules: RuleRef[];
  transit_days: string | null;
}

export interface CompetitorComparison {
  price: CompetitorPriceInput;
  comparable: boolean;
  reason: string;
  gap: string | null;
}

export interface MarketResult {
  market: string;
  currency: string;
  fx_rate: string | null;
  price_includes_vat: boolean | null;
  status: 'ok' | 'all_not_applicable' | 'missing_config' | 'needs_input';
  reasons: string[];
  options: OptionResult[];
  competitors: CompetitorComparison[];
}

export interface CalcResult {
  model_version: string;
  calc_date: string;
  mode: PricingMode;
  markets: MarketResult[];
  excluded_fees: string[];
}

export interface CalcResponse {
  calc_date: string;
  result: CalcResult;
  rule_refs: (RuleRef & { payload: unknown })[];
}

export interface SelectionOut {
  market: string;
  version_id: number;
  option_key: string;
  note: string | null;
  selected_at: string;
}

export interface VersionBrief {
  id: number;
  version_no: number;
  calc_date: string;
  model_version: string;
  created_at: string;
}

export interface Sheet {
  id: number;
  product_name: string;
  sku: string | null;
  asin: string | null;
  source_url: string | null;
  status: 'draft' | 'saved';
  draft_input: Partial<PricingInput> | null;
  current_version_id: number | null;
  lock_version: number;
  created_at: string;
  updated_at: string;
  markets: string[];
  rules_updated: boolean;
  selections: SelectionOut[];
  versions: VersionBrief[];
}

export interface SheetMeta {
  product_name: string;
  sku?: string | null;
  asin?: string | null;
  source_url?: string | null;
}

export interface Version {
  id: number;
  sheet_id: number;
  version_no: number;
  calc_date: string;
  model_version: string;
  input: PricingInput;
  rule_refs: (RuleRef & { payload: unknown })[];
  result: CalcResult;
  created_at: string;
}

export interface Rule {
  id: number;
  kind: RuleKind;
  scope_key: string;
  version: number;
  status: RuleStatus;
  effective_from: string;
  effective_to: string | null;
  source: string;
  review_note: string | null;
  payload: Record<string, any>;
  payload_schema: number;
  retired_reason: string | null;
  lock_version: number;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  retired_at: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------- 十进制字符串工具

/** 把十进制字符串的小数点左移 `places` 位（纯字符串操作，不经过浮点）。 */
export function shiftDecimal(value: string, places: number): string {
  const v = value.trim();
  if (!/^-?\d*(\.\d*)?$/.test(v) || v === '' || v === '-' || v === '.') return v;
  const neg = v.startsWith('-');
  const body = neg ? v.slice(1) : v;
  const [intPart, fracPart = ''] = body.split('.');
  let digits = (intPart || '0') + fracPart;
  let point = (intPart || '0').length - places;
  if (point <= 0) {
    digits = '0'.repeat(1 - point) + digits;
    point = 1;
  } else if (point > digits.length) {
    digits = digits + '0'.repeat(point - digits.length);
  }
  let int = digits.slice(0, point).replace(/^0+(?=\d)/, '');
  let frac = digits.slice(point).replace(/0+$/, '');
  if (int === '') int = '0';
  const out = frac ? `${int}.${frac}` : int;
  return neg && out !== '0' ? `-${out}` : out;
}

/** 「15」→「0.15」：界面上的百分数转成后端的比例。空值原样返回 null。 */
export function pctToRate(pct: string | number | null | undefined): string | null {
  if (pct === null || pct === undefined || `${pct}`.trim() === '') return null;
  return shiftDecimal(`${pct}`, 2);
}

/** 「0.15」→「15」。 */
export function rateToPct(rate: string | null | undefined): string | null {
  if (rate === null || rate === undefined || rate === '') return null;
  return shiftDecimal(rate, -2);
}

/** 十进制字符串四舍五入到 `dp` 位小数（半数进位，远离零）。 */
export function roundDecimal(value: string, dp: number): string {
  const neg = value.startsWith('-');
  const body = neg ? value.slice(1) : value;
  const [i, f = ''] = body.split('.');
  if (f.length <= dp) {
    const out = dp > 0 ? `${i}.${f.padEnd(dp, '0')}` : i;
    return neg ? `-${out}` : out;
  }
  const keep = (i + f.slice(0, dp)).split('').map(Number);
  if (Number(f[dp]) >= 5) {
    let k = keep.length - 1;
    while (k >= 0) {
      if (keep[k] === 9) {
        keep[k] = 0;
        k -= 1;
      } else {
        keep[k] += 1;
        break;
      }
    }
    if (k < 0) keep.unshift(1);
  }
  const s = keep.join('');
  const intLen = s.length - dp;
  const out = dp > 0 ? `${s.slice(0, intLen) || '0'}.${s.slice(intLen)}` : s;
  const isZero = /^0*\.?0*$/.test(out);
  return neg && !isZero ? `-${out}` : out;
}

/** 比例展示为百分数，保留两位小数（四舍五入在字符串上做，不经过浮点）。 */
export function formatPct(rate: string | null | undefined): string {
  if (rate === null || rate === undefined) return '—';
  return `${roundDecimal(shiftDecimal(rate, -2), 2)}%`;
}

/** 金额展示：两位小数。 */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  return roundDecimal(value, 2);
}

// ---------------------------------------------------------------- 接口

export async function getPricingMeta() {
  return request<PricingMeta>('/api/pricing/meta', { method: 'GET' });
}

export async function calculatePricing(input: PricingInput) {
  return request<CalcResponse>('/api/pricing/calculate', { method: 'POST', data: input });
}

export async function listSheets(body: {
  page?: number;
  page_size?: number;
  q?: string;
  market?: string;
  status?: 'draft' | 'saved';
}) {
  return request<Paged<Sheet>>('/api/pricing/sheets/list', { method: 'POST', data: body });
}

export async function getSheet(id: number) {
  return request<Sheet>(`/api/pricing/sheets/${id}`, { method: 'GET' });
}

export async function createSheet(body: SheetMeta & { draft_input?: Partial<PricingInput> | null }) {
  return request<Sheet>('/api/pricing/sheets', { method: 'POST', data: body });
}

export async function updateSheet(
  id: number,
  body: Partial<SheetMeta> & { lock_version: number; draft_input?: Partial<PricingInput> | null },
) {
  return request<Sheet>(`/api/pricing/sheets/${id}`, { method: 'PATCH', data: body });
}

export async function deleteSheet(id: number) {
  return request<{ ok: boolean }>(`/api/pricing/sheets/${id}`, { method: 'DELETE' });
}

export async function copySheet(id: number) {
  return request<Sheet>(`/api/pricing/sheets/${id}/copy`, { method: 'POST' });
}

export async function saveVersion(id: number, body: { lock_version: number; client_token: string; input: PricingInput }) {
  return request<Version>(`/api/pricing/sheets/${id}/versions`, { method: 'POST', data: body });
}

export async function getVersion(sheetId: number, versionId: number) {
  return request<Version>(`/api/pricing/sheets/${sheetId}/versions/${versionId}`, { method: 'GET' });
}

export function exportVersionUrl(sheetId: number, versionId: number) {
  return `/api/pricing/sheets/${sheetId}/versions/${versionId}/export`;
}

export async function exportVersion(sheetId: number, versionId: number) {
  return request<Blob>(exportVersionUrl(sheetId, versionId), { method: 'GET', responseType: 'blob' });
}

export async function selectOption(
  sheetId: number,
  market: string,
  body: { version_id: number; option_key: string; note?: string | null },
) {
  return request<Sheet>(`/api/pricing/sheets/${sheetId}/selections/${market}`, { method: 'PUT', data: body });
}

export async function clearSelection(sheetId: number, market: string) {
  return request<Sheet>(`/api/pricing/sheets/${sheetId}/selections/${market}`, { method: 'DELETE' });
}

export async function listRules(body: {
  kind?: RuleKind;
  scope_key?: string;
  status?: RuleStatus;
  page?: number;
  page_size?: number;
}) {
  return request<Paged<Rule>>('/api/pricing/rules/list', { method: 'POST', data: body });
}

export async function createRule(body: {
  kind: RuleKind;
  scope_key: string;
  effective_from: string;
  effective_to?: string | null;
  source: string;
  review_note?: string | null;
  payload: Record<string, any>;
}) {
  return request<Rule>('/api/pricing/rules', { method: 'POST', data: body });
}

export async function updateRule(
  id: number,
  body: {
    lock_version: number;
    effective_from?: string;
    effective_to?: string | null;
    clear_effective_to?: boolean;
    source?: string;
    review_note?: string | null;
    payload?: Record<string, any>;
  },
) {
  return request<Rule>(`/api/pricing/rules/${id}`, { method: 'PATCH', data: body });
}

export async function deleteRule(id: number, lockVersion: number) {
  return request<{ ok: boolean }>(`/api/pricing/rules/${id}`, {
    method: 'DELETE',
    params: { lock_version: lockVersion },
  });
}

export async function activateRule(id: number, lockVersion: number) {
  return request<Rule>(`/api/pricing/rules/${id}/activate`, { method: 'POST', data: { lock_version: lockVersion } });
}

export async function endRule(id: number, lockVersion: number, effectiveTo: string) {
  return request<Rule>(`/api/pricing/rules/${id}/end`, {
    method: 'POST',
    data: { lock_version: lockVersion, effective_to: effectiveTo },
  });
}

export async function retireRule(id: number, lockVersion: number, reason: string) {
  return request<Rule>(`/api/pricing/rules/${id}/retire`, {
    method: 'POST',
    data: { lock_version: lockVersion, reason },
  });
}

export async function copyRule(id: number) {
  return request<Rule>(`/api/pricing/rules/${id}/copy`, { method: 'POST' });
}

/** 防重复提交令牌：一次「保存」点击生成一个，网络重试复用同一个。 */
export function newClientToken(): string {
  const c = (globalThis as any).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, '');
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}
