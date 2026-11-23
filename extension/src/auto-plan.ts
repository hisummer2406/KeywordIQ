// 自动采集里可以脱离 Chrome API 独立验证的纯逻辑：店铺页 URL、店间间隔、拦截页识别、
// 每页结果之后的下一步决策（design 决策 2、4）。状态机本身在 auto-runner.ts。

import { mergeItemsWithCap } from './capture-accumulator';
import type { ParsedItem } from './parse-storefront';
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
export function detectBlockedPage(doc: Document): boolean {
  if (doc.querySelector('form[action*="validateCaptcha"], #captchacharacters')) return true;
  const text = (doc.body?.innerText ?? doc.body?.textContent ?? '').slice(0, 5000);
  return BLOCKED_TEXT_PATTERNS.some((pattern) => pattern.test(text));
}

// 页头「Deliver to」下面那一行（如 "New York 10001"、"China"）。选择器未在全部站点真实页面验证，
// 对不上只需要改这里；读不到时返回 undefined，判定为 unknown，不据此停止。
const DELIVERY_SELECTORS = ['#glow-ingress-line2', '#nav-global-location-popover-link'];

/** 页头显示的配送地址文字，读不到返回 undefined。纯函数，只读 `Document`。 */
export function parseDeliveryText(doc: Document): string | undefined {
  for (const selector of DELIVERY_SELECTORS) {
    const text = doc.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 120);
  }
  return undefined;
}

// 各站点的邮编形态：配送地址文字里能找到本国邮编，才算设成了该站点所在国。
const SITE_POSTCODE: Record<string, RegExp> = {
  US: /\b\d{5}(?:-\d{4})?\b/,
  DE: /\b\d{5}\b/,
  GB: /\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b/i,
};

/**
 * 配送地址是否与站点相符（试点 2026-11-24 实测：配送到中国时，多数店铺页显示 No results，
 * 少数只剩能国际配送的几个商品，会被误当成「店里只有这些」）。
 * - match：文字里有该站点国家的邮编；
 * - mismatch：读到了文字，但没有邮编（如 "China"）；
 * - unknown：读不到配送地址，或站点没有登记邮编形态。
 */
export function deliveryMatchesSite(text: string | undefined, site: string): 'match' | 'mismatch' | 'unknown' {
  const pattern = SITE_POSTCODE[site];
  if (!text || !pattern) return 'unknown';
  return pattern.test(text) ? 'match' : 'mismatch';
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
      /** 页头显示的配送地址，用于判断是否设成了站点所在国（见 deliveryMatchesSite）。 */
      deliveryText?: string;
    };

export type StoreFailureReason = 'empty_page' | 'over_cap' | 'bad_site';

export type PageDecision =
  | { action: 'stop_run'; reason: 'blocked' | 'wrong_delivery' }
  | { action: 'fail_store'; reason: StoreFailureReason }
  | { action: 'next_page'; items: ParsedItem[] }
  | { action: 'report'; items: ParsedItem[] };

/**
 * 拿到第 `page` 页的解析结果后该做什么（design 决策 4）：
 * - 拦截页 → 整轮停止；
 * - 配送地址不是站点所在国 → 整轮停止（否则大量店铺会被读成空或只剩几个商品）；
 * - 第 1 页为空 → 这家店失败，不上报（不能用空结果落定基线）；
 * - 后续页为空 → 视为到底，用已合并的结果上报；
 * - 合并后超过单批上限 → 这家店失败，不截断、不分批；
 * - 还没到 N 页且页面提示有下一页 → 继续翻页，否则上报。
 */
export function decideAfterPage(input: {
  scan: PageScan;
  page: number;
  maxPages: number;
  accumulated: ParsedItem[];
  cap: number;
  /** 站点码；不传则不做配送地址检查。 */
  site?: string;
}): PageDecision {
  const { scan, page, maxPages, accumulated, cap, site } = input;
  if (scan.kind === 'blocked') return { action: 'stop_run', reason: 'blocked' };
  if (site && deliveryMatchesSite(scan.deliveryText, site) === 'mismatch') {
    return { action: 'stop_run', reason: 'wrong_delivery' };
  }

  if (scan.items.length === 0) {
    if (page === 1) return { action: 'fail_store', reason: 'empty_page' };
    return { action: 'report', items: accumulated };
  }

  const merged = mergeItemsWithCap(accumulated, scan.items, cap);
  if (merged.rejectedByCap) return { action: 'fail_store', reason: 'over_cap' };

  if (page < maxPages && scan.hasNext) return { action: 'next_page', items: merged.items };
  return { action: 'report', items: merged.items };
}
