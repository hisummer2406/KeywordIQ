import { describe, expect, it } from 'vitest';
import { buildPageContext, mergeItems, pendingItemsKey } from '../src/capture-accumulator';

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
