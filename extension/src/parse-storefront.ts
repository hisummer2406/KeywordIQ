// 解析 Amazon 店铺全品列表页（`me=` 页面复用标准搜索结果页模板）的商品卡片。
//
// **选择器只能对着真实页面写和调，本文件写的是基于公开可观察到的 Amazon 搜索结果
// 页结构（`data-component-type="s-search-result"` + `data-asin` 属性）的最佳判断，
// 尚未在真实登录会话下验证过**——这不是本变更能自动化解决的事（design.md 决策 4）。
// tasks.md 第 8 节的真机验证若发现选择器不匹配，只需要改这一个文件。

import { usableSelectors } from './selector-overrides';

export interface ParsedItem {
  asin: string;
  title?: string;
  imageUrl?: string;
}

const RESULT_CARD_SELECTOR = '[data-component-type="s-search-result"][data-asin]';
const NEXT_PAGE_SELECTOR = 'a.s-pagination-next:not(.s-pagination-disabled)';
// 以下几个都允许自动采集传入服务端下发的覆盖（extension-remote-selectors）；
// 人工面板不传，行为与之前完全一致。

/**
 * 解析当前页面可见的商品卡片。纯函数：只读 `Document`，不发起任何请求、不依赖
 * 全局状态，方便用手工保存的 fixture 做单元测试。
 *
 * ASIN 为空的卡片（结构异常/广告位）跳过，不计入结果，也不当作解析失败。
 */
export function parseStorefrontPage(doc: Document, cardSelectors?: string[]): ParsedItem[] {
  let cards: HTMLElement[] = [];
  // 取第一个能匹配到卡片的选择器；卡片仍读 data-asin
  for (const selector of usableSelectors(cardSelectors, [RESULT_CARD_SELECTOR], doc)) {
    cards = Array.from(doc.querySelectorAll<HTMLElement>(selector));
    if (cards.length > 0) break;
  }
  const items: ParsedItem[] = [];

  for (const card of cards) {
    const asin = card.getAttribute('data-asin')?.trim().toUpperCase();
    if (!asin) continue;

    const titleEl = card.querySelector('h2 span, h2 a span');
    const title = titleEl?.textContent?.trim() || undefined;

    const imageEl = card.querySelector<HTMLImageElement>('img.s-image');
    const imageUrl = imageEl?.getAttribute('src')?.trim() || undefined;

    items.push({ asin, title, imageUrl });
  }

  return items;
}

/** 当前页是否存在"下一页"——只做提示用，插件不自动翻页（design.md）。 */
export function hasNextPage(doc: Document, selectors?: string[]): boolean {
  return usableSelectors(selectors, [NEXT_PAGE_SELECTOR], doc).some((s) => doc.querySelector(s) !== null);
}

// 卖家店名（#166 D5）。**以下候选选择器同样未在真实页面验证过**（与上面的商品选择器同一处境），
// 由 tasks 11.4 的真实页面人工验收确认；对不上只需要改这个列表。取不到就返回 undefined——
// 店名缺失不是失败，界面会回落显示 seller_id，绝不拿 seller_id、页面标题等文本拼凑一个店名。
const SELLER_NAME_SELECTORS = [
  // 店铺列表页左侧「卖家」筛选里当前选中的那一项
  '#p_6 li .a-text-bold',
  '#sellerName',
  '[data-testid="seller-name"]',
];
const MAX_SELLER_NAME_LEN = 200;

/** 尽力解析当前店铺页上的卖家店名。纯函数，只读 `Document`。 */
export function parseSellerName(doc: Document, selectors?: string[]): string | undefined {
  for (const selector of usableSelectors(selectors, SELLER_NAME_SELECTORS, doc)) {
    const text = doc.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim();
    if (text && text.length <= MAX_SELLER_NAME_LEN) return text;
  }
  return undefined;
}
