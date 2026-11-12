import type { ChangeRow, DossierStatus, ManualCategory, PointStatus, PricingSummary, Verdict } from '@/services/opportunityDossier';

/**
 * 机会档案的标签与纯函数（change add-opportunity-dossier）。组件只负责摆放，口径相关的判断放这里便于测试。
 */

export const STATUS: Record<DossierStatus, { text: string; color: string }> = {
  researching: { text: '研究中', color: 'default' },
  advance: { text: '推进', color: 'success' },
  need_evidence: { text: '补证', color: 'warning' },
  watch: { text: '观察', color: 'processing' },
  reject: { text: '否决', color: 'error' },
};

export const VERDICTS: { value: Verdict; label: string; hint: string }[] = [
  { value: 'advance', label: '推进', hint: '证据够了，进入下一步（打样、询价、测款等）' },
  { value: 'need_evidence', label: '补证', hint: '方向可能成立，但缺关键证据，补齐后再定' },
  { value: 'watch', label: '观察', hint: '数据太少或趋势不明，到复查日期再看' },
  { value: 'reject', label: '否决', hint: '不做，写明原因供以后查阅' },
];

/** 必须带复查日期的决定（与后端 RECHECK_REQUIRED 一致） */
export const RECHECK_REQUIRED: Verdict[] = ['need_evidence', 'watch'];

export const CATEGORY: Record<ManualCategory, { text: string; hint: string }> = {
  search_volume: { text: '搜索量', hint: 'ABA 只有搜索频率排名，没有绝对搜索量' },
  sales: { text: '销量', hint: '竞品销量或销售额估算' },
  price: { text: '售价', hint: '竞品在 Amazon 的售价与价格带' },
  bsr: { text: 'BSR', hint: '商品在自身类目的销售排名' },
  reviews: { text: '评论', hint: '评论数、评分与差评要点' },
  supply_cost: { text: '供应成本', hint: '采购价、起订量、物流与头程' },
  trademark: { text: '商标', hint: '品牌名与关键词的商标风险' },
  other: { text: '其他', hint: '不属于以上类别的资料' },
};

export const POINT_STATUS: Record<PointStatus, string> = {
  R: '在榜',
  D: '掉榜',
  N: '该期报表已导入，未收录这个词',
  X: '该期报表未导入',
  O: '超出保留期',
};

export const ASK_ERROR: Record<string, string> = {
  not_configured: '大模型未配置',
  invalid_output: '模型输出不符合要求',
  interrupted: '执行中断，结果未知',
  timeout: '调用超时，结果未知',
  network: '网络中断，结果未知',
  upstream_5xx: '模型服务异常，结果未知',
  rate_limited: '模型服务限流',
  credential_invalid: '模型凭证无效',
  insufficient_balance: '模型账户余额不足',
  connect_failed: '连接不上模型服务',
};

export function formatRank(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : n.toLocaleString('en-US');
}

/**
 * 排名变化。排名数字越小越靠前：after < before 是上升。
 * 任一侧为空（不在榜、没有对比日）返回 null——不把缺失当成 0。
 */
export function rankDelta(before: number | null | undefined, after: number | null | undefined) {
  if (before === null || before === undefined || after === null || after === undefined) return null;
  const places = before - after;
  return { places: Math.abs(places), direction: places > 0 ? ('up' as const) : places < 0 ? ('down' as const) : ('flat' as const) };
}

/** 复查日期不晚于今天即待复查。两者都是后端给的 YYYY-MM-DD（北京时间），直接按字符串比较。 */
export function isDue(recheckDate: string | null | undefined, today: string): boolean {
  return !!recheckDate && recheckDate <= today;
}

/**
 * 把输入框里的多行文本拆成关键词：**只按换行拆分**，去空白、转小写、去重，保持输入顺序。
 *
 * 不按逗号、分号拆：ABA 关键词本身可能含逗号（如 `vitamins a, d and k`），按逗号拆会把一个词
 * 拆成两个，丢掉它的 ABA 证据与档案归属（PR #188 评审）。两处输入框都写明「每行一个」。
 */
export function parseKeywordInput(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const kw = raw.trim().replace(/\s+/g, ' ').toLowerCase();
    if (kw && !out.includes(kw)) out.push(kw);
  }
  return out;
}

/** 一行变化的文字说明。 */
export function describeChange(row: ChangeRow): string {
  if (row.state === 'added') return '新加入的词';
  if (row.state === 'removed') return '已从档案移除';
  const b = row.before;
  const a = row.after;
  if (!a?.resolved) return '保留期内仍未进入过榜单';
  if (!b?.resolved) return a.latest ? `首次有在榜记录，最新排名 ${formatRank(a.latest.ranking)}` : '已进入关键词库，尚无在榜记录';
  const parts: string[] = [];
  const d = rankDelta(b.latest?.ranking, a.latest?.ranking);
  if (d && b.latest && a.latest) {
    if (b.latest.date === a.latest.date) parts.push(`最新在榜期未变（${a.latest.date}，排名 ${formatRank(a.latest.ranking)}）`);
    else if (d.direction === 'flat') parts.push(`排名持平 ${formatRank(a.latest.ranking)}`);
    else parts.push(`排名 ${formatRank(b.latest.ranking)} → ${formatRank(a.latest.ranking)}（${d.direction === 'up' ? '上升' : '下降'} ${formatRank(d.places)} 名）`);
  } else if (!b.latest && a.latest) parts.push(`开始在榜，排名 ${formatRank(a.latest.ranking)}`);
  if (b.ranked_periods !== null && a.ranked_periods !== null && a.ranked_periods !== b.ranked_periods) {
    parts.push(`在榜期数 ${b.ranked_periods} → ${a.ranked_periods}`);
  }
  if (row.top1_changed) parts.push('点击第一的商品换了');
  return parts.length ? parts.join('；') : '没有变化';
}

export const PRICING_STATE: Record<PricingSummary['state'], { text: string; color: string }> = {
  none: { text: '未测算', color: 'warning' },
  draft_only: { text: '只有草稿', color: 'default' },
  calculated: { text: '已有测算', color: 'blue' },
};

export const MODE_LABEL: Record<string, string> = {
  target_profit: '按目标利润求售价',
  target_margin: '按目标利润率求售价',
  fixed_price: '按给定售价算利润',
  max_purchase: '按售价倒推采购价上限',
};

const CURRENCY_SIGN: Record<string, string> = { CNY: '¥', USD: '$', GBP: '£', EUR: '€' };

/**
 * 展示核价引擎给出的十进制字符串金额：只加千分位与币种符号，**不做任何计算或舍入**——
 * 金额只有核价引擎一个来源（change add-opportunity-feasibility 决策 2）。空值显示「—」。
 */
export function formatMoney(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const negative = value.startsWith('-');
  const [intPart, frac] = (negative ? value.slice(1) : value).split('.');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const sign = (currency && CURRENCY_SIGN[currency]) ?? (currency ? `${currency} ` : '');
  return `${negative ? '-' : ''}${sign}${grouped}${frac !== undefined ? `.${frac}` : ''}`;
}

/** 0~1 的小数字符串 → 百分数（两位小数）。只用于展示。 */
export function formatRate(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : '—';
}
