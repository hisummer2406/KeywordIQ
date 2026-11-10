// 多页采集里可以脱离 DOM 独立验证的那部分：合并去重、单批上限、request_id/
// captured_at 的冻结与复用、page_context 白名单提取、持久化 key 生成。
// content-script.ts 里剩下的部分（面板 DOM、chrome.storage 读写、事件绑定）无法
// 在 jsdom 之外可靠验证，走 tasks.md 第 8 节的人工真机验证（design.md 决策 4）；
// 这个模块把可以独立验证的逻辑拆出来，PR #68 两轮复审要求的回归测试落在这里。

import type { ParsedItem } from './parse-storefront';

const PENDING_ITEMS_PREFIX = 'keywordiq_pending_capture_v1';

// 只保留分页/排序这类无害的导航上下文，绝不透传完整查询串——真实 `me=` 链接
// 普遍带 `ds=` 等会话签名参数，仓库既有契约（分析查询屏「竞对 ASIN 上报」一节）
// 明确要求这类参数不得读取、存储或用于重建请求（PR #68 复审 P1）。参数名对应
// Amazon 标准分页（`page`）与排序（`s`）控件，未经真机核实，若与线上不符只需要
// 改这个白名单。
const PAGE_CONTEXT_ALLOWED_PARAMS = ['page', 's'];

// 单批上限**只来自服务端**（`GET /api/competitor/collector-config`，#166 D4），
// 由 content-script 打开面板时经 background 读取后传入 `mergeItemsWithCap`。不再有
// 本地镜像常量或手填值：手工镜像与服务端配置漂移时，插件要么过早拒绝、要么攒到
// 超过真实上限才在提交时收到一个无法自行修复的 422（PR #68 二次复审 P1）。

/** 同一 (site, sellerId) 的"已采集未提交"批次在 chrome.storage.local 里的键。 */
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

/** 按 ASIN（大写、去首尾空白后）合并去重，保留先出现者的 title/imageUrl。 */
export function mergeItems(existing: ParsedItem[], incoming: ParsedItem[]): ParsedItem[] {
  const seen = new Map<string, ParsedItem>();
  for (const item of [...existing, ...incoming]) {
    const asin = item.asin.trim().toUpperCase();
    if (!asin || seen.has(asin)) continue;
    seen.set(asin, item);
  }
  return Array.from(seen.values());
}

export interface MergeWithCapResult {
  items: ParsedItem[];
  /**
   * true 表示本次合并会让条目数超过单次上报上限，因而被整体拒绝——`items`
   * 与调用前的 `existing` 完全相同，`incoming` 没有任何条目被并入。与仓库
   * 既有的"超限整批拒绝，不截断"纪律一致：不做部分合并，那会制造出一份
   * "看起来采集完整、实际悄悄丢了几个商品"的批次，且没有任何提示。
   */
  rejectedByCap: boolean;
}

/**
 * 用 {@link mergeItems} 合并，若结果会超过上限则整体拒绝、保留原有条目不变。
 *
 * **已知限制，本变更不解决**：单个卖家的商品总数一旦超过这个上限，插件就没有
 * 办法在一次会话里把它完整采集完——分成多批分别提交会重新引入"首批建立基线、
 * 后续批次的存量商品被误判成新发现"的问题（PR #68 复审 P1 修的就是这个），
 * 而这里的整批拒绝只保证不出现无法自行修复的 422，不保证能采完整个店铺
 * （PR #68 二次复审 P1，两个可接受方案之一）。
 */
export function mergeItemsWithCap(
  existing: ParsedItem[],
  incoming: ParsedItem[],
  cap: number,
): MergeWithCapResult {
  const merged = mergeItems(existing, incoming);
  if (merged.length > cap) {
    return { items: existing, rejectedByCap: true };
  }
  return { items: merged, rejectedByCap: false };
}

export interface FrozenRequest {
  requestId: string;
  capturedAt: string;
}

/**
 * 幂等键冻结：已有值就原样复用，否则生成一对新的。
 *
 * spec「`request_id` 在一次采集结果被冻结为请求体时生成、重试复用、重新采集
 * 换新」在两步流程下的落地：content-script.ts 只在**首次**为当前累积批次调用
 * 提交时调这个函数（此时 `existing` 的两个字段还是 `null`），生成的一对值随
 * 累积条目一起持久化；提交失败后运营不经过重新采集、直接再点"提交上报"，
 * `existing` 已经带着上次生成的值，原样复用——不这样做的话，网络重试全部耗尽
 * 后的手动重新提交会被当成一次全新观测，绕开幂等保护，服务端可能因此收到两条
 * 对应同一次真实采集的记录（PR #68 二次复审 P1）。
 *
 * 只要点过一次"采集本页"（无论合并结果是否带来新条目），content-script.ts
 * 都会把 `existing` 的两个字段重置为 `null`，下次提交因此必定生成新的一对——
 * 这与"无论内容是否变化"的 spec 原文一致，也是这个函数只在"两个字段都非空"
 * 时才复用、其余一律新生成的原因。
 */
export function freezeRequest(existing: {
  requestId: string | null;
  capturedAt: string | null;
}): FrozenRequest {
  if (existing.requestId && existing.capturedAt) {
    return { requestId: existing.requestId, capturedAt: existing.capturedAt };
  }
  return { requestId: crypto.randomUUID(), capturedAt: new Date().toISOString() };
}
