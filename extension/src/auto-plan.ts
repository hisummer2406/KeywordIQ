// 自动采集里可以脱离 Chrome API 独立验证的纯逻辑：店铺页 URL、店间间隔、拦截页识别、
// 每页结果之后的下一步决策（design 决策 2、4）。状态机本身在 auto-runner.ts。

import { mergeItemsWithCap } from './capture-accumulator';
import type { ParsedItem } from './parse-storefront';
import { usableSelectors } from './selector-overrides';
import { DOMAIN_TO_SITE, MARKETPLACE_ID_TO_SITE } from './site-map';

/** 店与店之间的间隔下限/上限（毫秒）。下限 30 秒恰好是 chrome.alarms 的最小间隔。 */
export const STORE_GAP_MS = { min: 30_000, max: 60_000 } as const;

/** 页面加载后多久拿不到解析结果就按访问受限停止整轮（design 决策 3）。 */
export const PAGE_TIMEOUT_MS = 90_000;

function invert(map: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(map).map(([k, v]) => [v, k]));
}

const SITE_TO_DOMAIN = invert(DOMAIN_TO_SITE);
const SITE_TO_MARKETPLACE = invert(MARKETPLACE_ID_TO_SITE);

/**
 * 按最新排序的店铺列表页 URL。`i=merchant-items` 必须带：content script 只在带它的
 * `me=` 页面激活（spec「只在店铺全品列表页激活」）。站点未登记返回 null，不猜默认。
 */
export function buildStorefrontUrl(sellerId: string, site: string, page: number): string | null {
  const domain = SITE_TO_DOMAIN[site];
  const marketplaceId = SITE_TO_MARKETPLACE[site];
  if (!domain || !marketplaceId) return null;
  const params = new URLSearchParams({
    i: 'merchant-items',
    me: sellerId,
    marketplaceID: marketplaceId,
    s: 'date-desc-rank',
  });
  if (page > 1) params.set('page', String(page));
  return `https://www.${domain}/s?${params.toString()}`;
}

/** 店间间隔：在 [min, max] 里取随机值，`random` 可注入以便测试。 */
export function pickStoreGapMs(random: () => number = Math.random): number {
  const span = STORE_GAP_MS.max - STORE_GAP_MS.min;
  return STORE_GAP_MS.min + Math.floor(random() * span);
}

const BLOCKED_SELECTORS = ['form[action*="validateCaptcha"]', '#captchacharacters'];

const BLOCKED_TEXT_PATTERNS = [
  /access restricted/i,
  /unauthorized ai agent/i,
  /robot check/i,
  /enter the characters you see below/i,
  /sorry, we just need to make sure you're not a robot/i,
];

/**
 * 页面是否是验证码或访问拒绝页。只用于「停止整轮」的判断，宁可误判为受限也不继续访问。
 * 选择器与文案未在全部站点的真实拦截页上验证（tasks 3.4），对不上只需要改这里。
 */
export function detectBlockedPage(
  doc: Document,
  overrides: { selectors?: string[]; texts?: string[] } = {},
): boolean {
  if (usableSelectors(overrides.selectors, BLOCKED_SELECTORS, doc).some((s) => doc.querySelector(s))) return true;
  const text = (doc.body?.innerText ?? doc.body?.textContent ?? '').slice(0, 5000);
  if (BLOCKED_TEXT_PATTERNS.some((pattern) => pattern.test(text))) return true;
  // 下发的拦截文案只当纯文本、不区分大小写，不构造正则
  const lower = text.toLowerCase();
  return (overrides.texts ?? []).some((t) => lower.includes(t.toLowerCase()));
}

// 页头「Deliver to」下面那一行（如 "New York 10001"、"China"）。选择器未在全部站点真实页面验证，
// 对不上只需要改这里。第二个是外层容器，只剩「Deliver to」提示时由 checkDelivery 判为未确认。
const DELIVERY_SELECTORS = ['#glow-ingress-line2', '#nav-global-location-popover-link'];

/** 页头显示的配送地址文字，读不到返回 undefined。纯函数，只读 `Document`。 */
export function parseDeliveryText(doc: Document, selectors?: string[]): string | undefined {
  for (const selector of usableSelectors(selectors, DELIVERY_SELECTORS, doc)) {
    const text = doc.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 120);
  }
  return undefined;
}

// 各站点的邮编形态：用来识别「读到了别的邮编」这种明确不符。
const SITE_POSTCODE: Record<string, RegExp> = {
  US: /\b\d{5}(?:-\d{4})?\b/,
  DE: /\b\d{5}\b/,
  GB: /\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b/i,
};

// 站点所在国在页头可能出现的写法；出现其它国家名即视为明确不符。
const SITE_COUNTRY: Record<string, RegExp> = {
  US: /\b(united states|usa|u\.s\.a?\.?)\b/i,
  DE: /\b(germany|deutschland)\b/i,
  GB: /\b(united kingdom|uk|great britain|england|scotland|wales)\b/i,
};

const OTHER_COUNTRY =
  /\b(china|hong kong|taiwan|japan|korea|singapore|india|canada|mexico|brazil|australia|germany|deutschland|france|italy|spain|netherlands|poland|sweden|belgium|united kingdom|great britain|united states|turkey|united arab emirates|saudi arabia|egypt|vietnam|thailand|malaysia|philippines|indonesia)\b/i;

// 外层容器里的固定提示文字，去掉后为空就说明没读到位置。
const DELIVERY_BOILERPLATE = /deliver(?:ing)? to|update location|select your address|liefern an|standort aktualisieren|hello,?/gi;

/**
 * 把页头配送文字和目标邮编比较（#205 阶段 1；2026-11-24 实测配送到中国时多数店铺页 No results，
 * 少数只剩能国际配送的几个商品，会被误当成「店里只有这些」）。
 * - match：文字里有目标邮编（整词）；
 * - mismatch：确认不符——出现了该站点格式的其它邮编，或出现了别国名称（如 "China"、"Berlin 10115, Germany"）；
 * - unconfirmed：读不到、只有「Deliver to」提示、没有配置目标邮编，或只读到无法判断的文字。
 */
export function checkDelivery(
  text: string | undefined,
  site: string,
  target: string | undefined,
): 'match' | 'mismatch' | 'unconfirmed' {
  if (!text || !target) return 'unconfirmed';
  const rest = text.replace(DELIVERY_BOILERPLATE, ' ').replace(/[\s,.:;\u200e\u200f-]+/g, ' ').trim();
  if (!rest) return 'unconfirmed';
  const escaped = target.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*');
  if (new RegExp(`(^|[^A-Za-z0-9])${escaped}($|[^A-Za-z0-9])`, 'i').test(text)) return 'match';
  const postcode = SITE_POSTCODE[site];
  if (postcode?.test(rest)) return 'mismatch';
  const countryMatch = rest.match(OTHER_COUNTRY);
  if (countryMatch && !SITE_COUNTRY[site]?.test(countryMatch[0])) return 'mismatch';
  return 'unconfirmed';
}

/**
 * content script 对一次解析请求的回答。`docId` 是回答者所在文档的随机标识，每次页面加载
 * 都不同：翻页后若仍是旧文档在回答（新页面还没加载完），后台据此识别并忽略（PR #206 Codex P2）。
 * 不用 URL 里的页码核对，因为 Amazon 可能改写翻页后的地址。
 */
export type PageScan =
  | { kind: 'blocked'; docId?: string }
  | {
      kind: 'ok';
      items: ParsedItem[];
      hasNext: boolean;
      sellerName?: string;
      docId?: string;
      /** 页头显示的配送地址，用于和目标邮编比较（见 checkDelivery）。 */
      deliveryText?: string;
    };

export type StoreFailureReason = 'empty_page' | 'missing_page' | 'over_cap' | 'bad_site';

/** 这家店的覆盖情况：自然到达末页，或达到 N 页上限时页面仍提示有下一页（覆盖有限）。 */
export type Coverage = 'complete' | 'limited';

export type PageDecision =
  | { action: 'stop_run'; reason: 'blocked' | 'wrong_delivery' | 'delivery_unconfirmed' }
  | { action: 'fail_store'; reason: StoreFailureReason }
  | { action: 'next_page'; items: ParsedItem[] }
  | { action: 'report'; items: ParsedItem[]; coverage: Coverage };

/**
 * 拿到第 `page` 页的解析结果后该做什么（design 决策 4）：
 * - 拦截页 → 整轮停止；
 * - 配送位置与目标邮编确认不符 → 整轮停止（wrong_delivery）；读不到或无法确认 → 整轮停止（delivery_unconfirmed），
 *   当前店不上报，已经提交的店保留；
 * - 第 1 页为空 → 这家店失败（empty_page），不上报（不能用空结果落定基线）；
 * - 后续页为空 → 前一页明确提示有下一页才会走到这里，属于异常缺页（missing_page），不提交残缺批次；
 * - 合并后超过单批上限 → 这家店失败，不截断、不分批；
 * - 页面提示有下一页且还没到 N 页 → 继续翻页；
 * - 没有下一页 → 自然到达末页（complete）上报；到了 N 页仍有下一页 → 覆盖有限（limited）上报。
 */
export function decideAfterPage(input: {
  scan: PageScan;
  page: number;
  maxPages: number;
  accumulated: ParsedItem[];
  cap: number;
  /** 站点码；不传则不做配送位置检查。 */
  site?: string;
  /** 该站点的目标邮编；传了 site 却没有目标时按无法确认处理。 */
  deliveryTarget?: string;
}): PageDecision {
  const { scan, page, maxPages, accumulated, cap, site, deliveryTarget } = input;
  if (scan.kind === 'blocked') return { action: 'stop_run', reason: 'blocked' };
  if (site) {
    const delivery = checkDelivery(scan.deliveryText, site, deliveryTarget);
    if (delivery === 'mismatch') return { action: 'stop_run', reason: 'wrong_delivery' };
    if (delivery === 'unconfirmed') return { action: 'stop_run', reason: 'delivery_unconfirmed' };
  }

  if (scan.items.length === 0) {
    return { action: 'fail_store', reason: page === 1 ? 'empty_page' : 'missing_page' };
  }

  const merged = mergeItemsWithCap(accumulated, scan.items, cap);
  if (merged.rejectedByCap) return { action: 'fail_store', reason: 'over_cap' };

  if (!scan.hasNext) return { action: 'report', items: merged.items, coverage: 'complete' };
  if (page < maxPages) return { action: 'next_page', items: merged.items };
  return { action: 'report', items: merged.items, coverage: 'limited' };
}
