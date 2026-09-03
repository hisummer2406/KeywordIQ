// 多页采集里可以脱离 DOM 独立验证的那部分：合并去重、page_context 白名单提取、
// 持久化 key 生成。content-script.ts 里剩下的部分（面板 DOM、chrome.storage 读写、
// 事件绑定）无法在 jsdom 之外可靠验证，走 tasks.md 第 8 节的人工真机验证
// （design.md 决策 4）；这个模块把可以独立验证的逻辑拆出来，PR #68 复审要求的
// 回归测试落在这里。

import type { ParsedItem } from './parse-storefront';

const PENDING_ITEMS_PREFIX = 'keywordiq_pending_capture_v1';

// 只保留分页/排序这类无害的导航上下文，绝不透传完整查询串——真实 `me=` 链接
// 普遍带 `ds=` 等会话签名参数，仓库既有契约（分析查询屏「竞对 ASIN 上报」一节）
// 明确要求这类参数不得读取、存储或用于重建请求（PR #68 复审 P1）。参数名对应
// Amazon 标准分页（`page`）与排序（`s`）控件，未经真机核实，若与线上不符只需要
// 改这个白名单。
const PAGE_CONTEXT_ALLOWED_PARAMS = ['page', 's'];

/** 同一 (site, sellerId) 的"已采集未提交"条目在 chrome.storage.local 里的键。 */
export function pendingItemsKey(site: string, sellerId: string): string {
  return `${PENDING_ITEMS_PREFIX}:${site}:${sellerId}`;
}

/** 从页面 URL 里按白名单摘取导航上下文，丢弃 `ds=` 等会话签名参数。 */
export function buildPageContext(url: URL): string | undefined {
  const kept = new URLSearchParams();
  for (const key of PAGE_CONTEXT_ALLOWED_PARAMS) {
    const value = url.searchParams.get(key);
    if (value) kept.set(key, value);
  }
  const serialized = kept.toString();
  return serialized || undefined;
}

/**
 * 按 ASIN（大写、去首尾空白后）合并去重，保留先出现者的 title/imageUrl。
 * 用于把新翻到的一页与此前已累积的条目合并为"本次采集结果"（spec「采集当前已
 * 加载页面的 ASIN 清单，支持翻页」——多页结果必须合并为一次上报，不能逐页各发
 * 一次请求：逐页上报会让第一页单独落定基线，第二页的存量商品因此被服务端判成
 * "新发现"，PR #68 复审 P1）。
 */
export function mergeItems(existing: ParsedItem[], incoming: ParsedItem[]): ParsedItem[] {
  const seen = new Map<string, ParsedItem>();
  for (const item of [...existing, ...incoming]) {
    const asin = item.asin.trim().toUpperCase();
    if (!asin || seen.has(asin)) continue;
    seen.set(asin, item);
  }
  return Array.from(seen.values());
}
