import { describe, expect, it } from 'vitest';
import { buildReportRequest, dedupeItems } from '../src/build-report';
import type { ParsedItem } from '../src/parse-storefront';

const baseInput = {
  sellerId: 'A1TZ946F8HZTNY',
  site: 'US',
  requestId: 'req-1',
  capturedAt: '2026-09-03T00:00:00.000Z',
};

describe('dedupeItems', () => {
  it('同一 ASIN（不同大小写/空白）只保留一条，取第一次出现的展示属性', () => {
    const items: ParsedItem[] = [
      { asin: ' b0testasin1 ', title: 'First', imageUrl: 'https://img/1.jpg' },
      { asin: 'B0TESTASIN1', title: 'Second (should be dropped)' },
      { asin: 'B0TESTASIN2', title: 'Other' },
    ];
    const result = dedupeItems(items);

    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({
      asin: 'B0TESTASIN1',
      title: 'First',
      image_url: 'https://img/1.jpg',
    });
    expect(result[1]?.asin).toBe('B0TESTASIN2');
  });

  it('空数组返回空数组', () => {
    expect(dedupeItems([])).toEqual([]);
  });
});

describe('buildReportRequest', () => {
  it('字段形状与 AsinReportRequest 一致（snake_case，含全部必填字段）', () => {
    const body = buildReportRequest({
      ...baseInput,
      items: [{ asin: 'B0TESTASIN1', title: 'T', imageUrl: 'https://img/1.jpg' }],
      shipToRegion: 'US',
      pageContext: 'sort=date-desc-rank&page=1',
    });

    expect(body).toEqual({
      request_id: 'req-1',
      seller_id: 'A1TZ946F8HZTNY',
      site: 'US',
      captured_at: '2026-09-03T00:00:00.000Z',
      ship_to_region: 'US',
      page_context: 'sort=date-desc-rank&page=1',
      items: [{ asin: 'B0TESTASIN1', title: 'T', image_url: 'https://img/1.jpg' }],
    });
  });

  it('可选字段缺省时不硬塞空字符串（保持 undefined，序列化后 JSON 里不出现该键）', () => {
    const body = buildReportRequest({ ...baseInput, items: [] });
    expect(body.ship_to_region).toBeUndefined();
    expect(body.page_context).toBeUndefined();
  });
});

describe('seller_name（#166 D5）', () => {
  it('有店名时带上，去首尾空白', () => {
    const body = buildReportRequest({ ...baseInput, items: [{ asin: 'B0TESTASIN1' }], sellerName: ' Acme ' });
    expect(body.seller_name).toBe('Acme');
  });

  it('没有或只有空白时不带', () => {
    expect(buildReportRequest({ ...baseInput, items: [{ asin: 'B0TESTASIN1' }] }).seller_name).toBeUndefined();
    expect(
      buildReportRequest({ ...baseInput, items: [{ asin: 'B0TESTASIN1' }], sellerName: '  ' }).seller_name,
    ).toBeUndefined();
  });
});
