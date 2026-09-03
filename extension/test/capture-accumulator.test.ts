import { describe, expect, it } from 'vitest';
import {
  MAX_REPORT_ITEMS,
  buildPageContext,
  freezeRequest,
  mergeItems,
  mergeItemsWithCap,
  pendingItemsKey,
} from '../src/capture-accumulator';
import type { ParsedItem } from '../src/parse-storefront';

describe('mergeItems', () => {
  it('按 ASIN（大写、去首尾空白后）去重，保留先出现者的 title/imageUrl', () => {
    const merged = mergeItems(
      [{ asin: 'B0AAA11111', title: '第一页标题' }],
      [
        { asin: ' b0aaa11111 ', title: '第二页标题（应被忽略）' },
        { asin: 'B0BBB22222', title: '第二页新品' },
      ],
    );

    expect(merged).toHaveLength(2);
    expect(merged.find((i) => i.asin === 'B0AAA11111')).toEqual({
      asin: 'B0AAA11111',
      title: '第一页标题',
    });
    expect(merged.find((i) => i.asin === 'B0BBB22222')).toBeDefined();
  });

  it('翻页后再次采集：第二页新条目并入，第一页已采集的条目不被覆盖或丢弃', () => {
    const page1 = [{ asin: 'B0PAGE0001' }, { asin: 'B0PAGE0002' }];
    const page2 = [{ asin: 'B0PAGE0003' }];

    const afterPage2 = mergeItems(page1, page2);

    expect(afterPage2.map((i) => i.asin).sort()).toEqual(['B0PAGE0001', 'B0PAGE0002', 'B0PAGE0003']);
  });

  it('空 ASIN（含仅空白）被跳过', () => {
    expect(mergeItems([], [{ asin: '  ' }, { asin: '' }])).toEqual([]);
  });
});

describe('buildPageContext', () => {
  it('只保留白名单参数（page/s），丢弃 ds= 等会话签名参数', () => {
    const url = new URL(
      'https://www.amazon.com/s?i=merchant-items&me=A1&ds=SUPER_SECRET_SESSION_SIG&page=2&s=price-asc-rank',
    );

    expect(buildPageContext(url)).toBe('page=2&s=price-asc-rank');
  });

  it('白名单参数均缺失时返回 undefined，不返回空字符串', () => {
    const url = new URL('https://www.amazon.com/s?i=merchant-items&me=A1&ds=SUPER_SECRET_SESSION_SIG');

    expect(buildPageContext(url)).toBeUndefined();
  });

  it('完整查询串不会原样出现在结果里', () => {
    const url = new URL('https://www.amazon.com/s?i=merchant-items&me=A1&ds=SUPER_SECRET_SESSION_SIG&page=3');

    expect(buildPageContext(url)).not.toContain('ds=');
    expect(buildPageContext(url)).not.toContain('SUPER_SECRET_SESSION_SIG');
  });
});

describe('pendingItemsKey', () => {
  it('不同站点或不同卖家生成互不相同的 key', () => {
    expect(pendingItemsKey('US', 'A1')).not.toBe(pendingItemsKey('GB', 'A1'));
    expect(pendingItemsKey('US', 'A1')).not.toBe(pendingItemsKey('US', 'A2'));
  });
});

describe('mergeItemsWithCap', () => {
  function makeItems(count: number, prefix: string): ParsedItem[] {
    return Array.from({ length: count }, (_, i) => ({ asin: `${prefix}${String(i).padStart(6, '0')}` }));
  }

  it('合并后不超过上限时正常合并', () => {
    const result = mergeItemsWithCap([{ asin: 'B0AAA11111' }], [{ asin: 'B0BBB22222' }], 10);
    expect(result).toEqual({ items: [{ asin: 'B0AAA11111' }, { asin: 'B0BBB22222' }], rejectedByCap: false });
  });

  it('合并后超过上限时整批拒绝，保留原有条目不变，一个新条目都不并入', () => {
    const existing = makeItems(8, 'B0OLD');
    const incoming = makeItems(5, 'B0NEW'); // 8 + 5 = 13 > cap 10

    const result = mergeItemsWithCap(existing, incoming, 10);

    expect(result.rejectedByCap).toBe(true);
    expect(result.items).toEqual(existing);
    expect(result.items.some((i) => i.asin.startsWith('B0NEW'))).toBe(false);
  });

  it('合并后恰好等于上限时不拒绝', () => {
    const existing = makeItems(5, 'B0OLD');
    const incoming = makeItems(5, 'B0NEW');

    const result = mergeItemsWithCap(existing, incoming, 10);

    expect(result.rejectedByCap).toBe(false);
    expect(result.items).toHaveLength(10);
  });

  it('默认上限是 200，与 .env.example 的 COMPETITOR_MAX_REPORT_ITEMS 默认值一致', () => {
    expect(MAX_REPORT_ITEMS).toBe(200);
  });
});

describe('freezeRequest', () => {
  it('已有冻结值时原样复用，不重新生成', () => {
    const frozen = freezeRequest({ requestId: 'existing-req', capturedAt: '2026-01-01T00:00:00.000Z' });
    expect(frozen).toEqual({ requestId: 'existing-req', capturedAt: '2026-01-01T00:00:00.000Z' });
  });

  it('requestId 或 capturedAt 任一缺失都视为未冻结，生成新的一对', () => {
    const a = freezeRequest({ requestId: null, capturedAt: null });
    const b = freezeRequest({ requestId: 'x', capturedAt: null });
    const c = freezeRequest({ requestId: null, capturedAt: '2026-01-01T00:00:00.000Z' });

    for (const frozen of [a, b, c]) {
      expect(typeof frozen.requestId).toBe('string');
      expect(frozen.requestId.length).toBeGreaterThan(0);
      expect(typeof frozen.capturedAt).toBe('string');
    }
  });

  it('两次独立生成（均未冻结）得到不同的 requestId——这是"重新采集"的语义', () => {
    const first = freezeRequest({ requestId: null, capturedAt: null });
    const second = freezeRequest({ requestId: null, capturedAt: null });
    expect(first.requestId).not.toBe(second.requestId);
  });
});
