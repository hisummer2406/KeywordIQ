// 组装符合 app/table/competitor/asin_schemas.py::AsinReportRequest 契约的请求体。
// 字段名严格对齐 Pydantic 模型的 snake_case 声明（该模型没有 alias 配置，
// JSON 请求体必须原样用这些字段名）。开工前重新读一遍那个文件确认字段形状，
// 不要凭这里的复述——契约变了这个模块要跟着改。

import type { ParsedItem } from './parse-storefront';

export interface AsinReportItem {
  asin: string;
  title?: string;
  image_url?: string;
}

export interface AsinReportRequest {
  request_id: string;
  seller_id: string;
  site: string;
  captured_at?: string;
  ship_to_region?: string;
  page_context?: string;
  items: AsinReportItem[];
}

/**
 * 按 ASIN（大写、去首尾空白后）去重，保留第一次出现的 title/image_url。
 * 服务端也会做这一步，但插件侧先去重能减少请求体大小，也方便预览"实际会提交
 * 多少条"。
 */
export function dedupeItems(items: ParsedItem[]): AsinReportItem[] {
  const seen = new Map<string, AsinReportItem>();
  for (const item of items) {
    const asin = item.asin.trim().toUpperCase();
    if (!asin || seen.has(asin)) continue;
    seen.set(asin, {
      asin,
      title: item.title,
      image_url: item.imageUrl,
    });
  }
  return Array.from(seen.values());
}

export interface BuildReportInput {
  sellerId: string;
  site: string;
  items: ParsedItem[];
  requestId: string;
  capturedAt: string;
  shipToRegion?: string;
  pageContext?: string;
}

export function buildReportRequest(input: BuildReportInput): AsinReportRequest {
  return {
    request_id: input.requestId,
    seller_id: input.sellerId,
    site: input.site,
    captured_at: input.capturedAt,
    ship_to_region: input.shipToRegion,
    page_context: input.pageContext,
    items: dedupeItems(input.items),
  };
}
