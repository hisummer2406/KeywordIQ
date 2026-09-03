// 解析 Amazon 店铺全品列表页（`me=` 页面复用标准搜索结果页模板）的商品卡片。
//
// **选择器只能对着真实页面写和调，本文件写的是基于公开可观察到的 Amazon 搜索结果
// 页结构（`data-component-type="s-search-result"` + `data-asin` 属性）的最佳判断，
// 尚未在真实登录会话下验证过**——这不是本变更能自动化解决的事（design.md 决策 4）。
// tasks.md 第 8 节的真机验证若发现选择器不匹配，只需要改这一个文件。

export interface ParsedItem {
  asin: string;
  title?: string;
  imageUrl?: string;
}

const RESULT_CARD_SELECTOR = '[data-component-type="s-search-result"][data-asin]';
const NEXT_PAGE_SELECTOR = 'a.s-pagination-next:not(.s-pagination-disabled)';

/**
 * 解析当前页面可见的商品卡片。纯函数：只读 `Document`，不发起任何请求、不依赖
 * 全局状态，方便用手工保存的 fixture 做单元测试。
 *
 * ASIN 为空的卡片（结构异常/广告位）跳过，不计入结果，也不当作解析失败。
 */
export function parseStorefrontPage(doc: Document): ParsedItem[] {
  const cards = Array.from(doc.querySelectorAll<HTMLElement>(RESULT_CARD_SELECTOR));
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
export function hasNextPage(doc: Document): boolean {
  return doc.querySelector(NEXT_PAGE_SELECTOR) !== null;
}
