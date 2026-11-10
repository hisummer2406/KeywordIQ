import {
  pctToRate,
  rateToPct,
  type CompetitorPriceInput,
  type MarketInput,
  type PricingInput,
  type PricingMode,
} from '@/services/pricing';

/**
 * 表单值 ↔ 核价输入的转换（纯函数）。
 *
 * 表单里比例用百分数（用户填 15 表示 15%），提交时转成比例字符串；数字输入框开
 * `stringMode`，值一路是字符串，不经过浮点。
 */

export interface MarketFormValues {
  price?: string | null;
  duty_pct?: string | null;
  ads_pct?: string | null;
  return_pct?: string | null;
  payout_pct?: string | null;
  storage_months?: string | null;
  headhaul_per_unit?: string | null;
  fba_fee_override?: string | null;
}

export interface CompetitorFormValues {
  market?: string;
  price?: string | null;
  includes_vat?: boolean;
  note?: string;
}

export interface PricingFormValues {
  product_name?: string;
  sku?: string;
  asin?: string;
  source_url?: string;
  mode: PricingMode;
  markets: string[];
  category?: string | null;
  purchase_cost?: string | null;
  packaging_labor?: string | null;
  weight_kg?: string | null;
  length_cm?: string | null;
  width_cm?: string | null;
  height_cm?: string | null;
  attributes: string[];
  liquid_ml?: string | null;
  bottles?: number | null;
  target_profit?: string | null;
  target_margin_pct?: string | null;
  /** 固定售价、倒推采购价两种模式下，目标按利润额还是利润率。 */
  target_type?: 'profit' | 'margin';
  declared_basis: 'purchase_cost' | 'fixed';
  declared_value?: string | null;
  include_fba: boolean;
  include_fbm: boolean;
  market_inputs?: Record<string, MarketFormValues | undefined>;
  competitor_prices?: CompetitorFormValues[];
}

export const DEFAULT_FORM_VALUES: PricingFormValues = {
  mode: 'target_profit',
  markets: [],
  attributes: ['general'],
  packaging_labor: '0',
  bottles: 1,
  target_type: 'profit',
  declared_basis: 'purchase_cost',
  include_fba: true,
  include_fbm: true,
};

const blank = (v: string | null | undefined) => v === null || v === undefined || `${v}`.trim() === '';
const str = (v: string | number | null | undefined) => (blank(v as string) ? null : `${v}`.trim());

function usesMargin(v: PricingFormValues): boolean {
  if (v.mode === 'target_margin') return true;
  if (v.mode === 'target_profit') return false;
  return v.target_type === 'margin';
}

/** 只把用户填了的字段放进去：没填 = 用市场默认值（后端据此区分「没填」与「填了 0」）。 */
function marketInput(m: MarketFormValues | undefined): MarketInput {
  const out: MarketInput = {};
  if (!m) return out;
  const set = (k: keyof MarketInput, v: string | null) => {
    if (v !== null) (out as Record<string, string>)[k] = v;
  };
  set('price', str(m.price));
  set('duty_rate', pctToRate(m.duty_pct));
  set('ads_rate', pctToRate(m.ads_pct));
  set('return_rate', pctToRate(m.return_pct));
  set('payout_rate', pctToRate(m.payout_pct));
  set('storage_months', str(m.storage_months));
  set('headhaul_per_unit', str(m.headhaul_per_unit));
  set('fba_fee_override', str(m.fba_fee_override));
  return out;
}

export function toPricingInput(
  v: PricingFormValues,
  currencies: Record<string, string>,
  priceOverrides: Record<string, string> = {},
): PricingInput {
  const liquid = v.attributes.includes('liquid') || v.attributes.includes('paste');
  const marketInputs: Record<string, MarketInput> = {};
  for (const site of v.markets) {
    const mi = marketInput(v.market_inputs?.[site]);
    if (Object.keys(mi).length) marketInputs[site] = mi;
  }
  const competitors: CompetitorPriceInput[] = (v.competitor_prices ?? [])
    .filter((c) => c.market && !blank(c.price) && v.markets.includes(c.market))
    .map((c) => ({
      market: c.market!,
      price: `${c.price}`.trim(),
      currency: currencies[c.market!] ?? '',
      includes_vat: !!c.includes_vat,
      note: c.note ?? '',
    }));
  const overrides: Record<string, string> = {};
  for (const [k, p] of Object.entries(priceOverrides)) {
    if (v.markets.includes(k.split('/')[0]) && !blank(p)) overrides[k] = p;
  }
  return {
    mode: v.mode,
    markets: v.markets,
    purchase_cost: v.mode === 'max_purchase' ? null : str(v.purchase_cost),
    packaging_labor: str(v.packaging_labor) ?? '0',
    weight_kg: str(v.weight_kg),
    length_cm: str(v.length_cm),
    width_cm: str(v.width_cm),
    height_cm: str(v.height_cm),
    attributes: v.attributes.length ? v.attributes : ['general'],
    liquid_ml: liquid ? str(v.liquid_ml) : null,
    bottles: liquid ? v.bottles ?? 1 : 1,
    category: v.category ?? null,
    target_profit: usesMargin(v) ? null : str(v.target_profit),
    target_margin: usesMargin(v) ? pctToRate(v.target_margin_pct) : null,
    declared_basis: v.declared_basis,
    declared_value: v.declared_basis === 'fixed' ? str(v.declared_value) : null,
    include_fba: v.include_fba,
    include_fbm: v.include_fbm,
    market_inputs: marketInputs,
    price_overrides: overrides,
    competitor_prices: competitors,
  };
}

/** 已保存的输入 → 表单值（打开历史记录时用）。 */
export function fromPricingInput(input: Partial<PricingInput> | null | undefined): {
  values: PricingFormValues;
  priceOverrides: Record<string, string>;
} {
  const i = input ?? {};
  const marketInputs: Record<string, MarketFormValues> = {};
  for (const [site, m] of Object.entries(i.market_inputs ?? {})) {
    marketInputs[site] = {
      price: m.price ?? null,
      duty_pct: rateToPct(m.duty_rate),
      ads_pct: rateToPct(m.ads_rate),
      return_pct: rateToPct(m.return_rate),
      payout_pct: rateToPct(m.payout_rate),
      storage_months: m.storage_months ?? null,
      headhaul_per_unit: m.headhaul_per_unit ?? null,
      fba_fee_override: m.fba_fee_override ?? null,
    };
  }
  return {
    values: {
      ...DEFAULT_FORM_VALUES,
      mode: i.mode ?? 'target_profit',
      markets: i.markets ?? [],
      category: i.category ?? null,
      purchase_cost: i.purchase_cost ?? null,
      packaging_labor: i.packaging_labor ?? '0',
      weight_kg: i.weight_kg ?? null,
      length_cm: i.length_cm ?? null,
      width_cm: i.width_cm ?? null,
      height_cm: i.height_cm ?? null,
      attributes: i.attributes ?? ['general'],
      liquid_ml: i.liquid_ml ?? null,
      bottles: i.bottles ?? 1,
      target_profit: i.target_profit ?? null,
      target_margin_pct: rateToPct(i.target_margin ?? null),
      target_type: i.target_margin && !i.target_profit ? 'margin' : 'profit',
      declared_basis: i.declared_basis ?? 'purchase_cost',
      declared_value: i.declared_value ?? null,
      include_fba: i.include_fba ?? true,
      include_fbm: i.include_fbm ?? true,
      market_inputs: marketInputs,
      competitor_prices: (i.competitor_prices ?? []).map((c) => ({
        market: c.market,
        price: c.price,
        includes_vat: c.includes_vat,
        note: c.note,
      })),
    },
    priceOverrides: { ...(i.price_overrides ?? {}) },
  };
}

/** 两次输入是否等价（判断结果是否过期）。 */
export function sameInput(a: PricingInput | null, b: PricingInput | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
