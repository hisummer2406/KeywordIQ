import { describe, expect, it } from 'vitest';
import {
  RANGE_PRESETS,
  detectSearchKind,
  emptyFilters,
  matchedPreset,
  toSearchBody,
  type ScopeDraft,
} from './filters';

const scope: ScopeDraft = { site: 'US', view: 'DAY', date: '2026-07-18' };

describe('detectSearchKind', () => {
  it('把 B0 + 8 位的输入判为 ASIN', () => {
    expect(detectSearchKind('B07Z82895W')).toBe('asin');
    expect(detectSearchKind('b06xztz7gb')).toBe('asin'); // 大小写不敏感
    expect(detectSearchKind('  B00RQHBZVS  ')).toBe('asin'); // 前后空白不影响判定
  });

  it('其余非空输入判为关键词', () => {
    expect(detectSearchKind('screen protector')).toBe('keyword');
    expect(detectSearchKind('B0')).toBe('keyword'); // 位数不够，不是 ASIN
    expect(detectSearchKind('B07Z82895W7')).toBe('keyword'); // 多一位，不是 ASIN
  });

  it('空输入不检索', () => {
    expect(detectSearchKind('')).toBe('none');
    expect(detectSearchKind('   ')).toBe('none');
  });
});

describe('toSearchBody', () => {
  it('检索框按判定结果落到 keyword 或 asin，另一个不传', () => {
    const asAsin = toSearchBody(scope, { ...emptyFilters(), q: 'B07Z82895W' });
    expect(asAsin.asin).toBe('B07Z82895W');
    expect(asAsin.keyword).toBeUndefined();

    const asKeyword = toSearchBody(scope, { ...emptyFilters(), q: 'screen protector' });
    expect(asKeyword.keyword).toBe('screen protector');
    expect(asKeyword.asin).toBeUndefined();
  });

  it('空条件只带作用域，其余字段一律 undefined', () => {
    const body = toSearchBody(scope, emptyFilters());
    expect(body.site).toBe('US');
    expect(body.view).toBe('DAY');
    expect(body.date).toBe('2026-07-18');
    const rest = Object.entries(body).filter(([k]) => !['site', 'view', 'date'].includes(k));
    expect(rest.every(([, v]) => v === undefined)).toBe(true);
  });

  it('空类目数组不传——空集合视同不筛，传 [] 会让后端把它当成带筛选', () => {
    expect(toSearchBody(scope, emptyFilters()).categories).toBeUndefined();
  });

  it('非空类目原样传出', () => {
    const body = toSearchBody(scope, {
      ...emptyFilters(),
      categories: ['Electronics', 'Pet Supplies'],
    });
    expect(body.categories).toEqual(['Electronics', 'Pet Supplies']);
  });

  it('文本条件裁掉纯空白', () => {
    const body = toSearchBody(scope, { ...emptyFilters(), productTitle: ' ACME ' });
    expect(body.product_title).toBe('ACME');
  });

  it('品牌是多选集合，空集合不传（与类目对称）', () => {
    expect(toSearchBody(scope, emptyFilters()).brands).toBeUndefined();
    const body = toSearchBody(scope, { ...emptyFilters(), brands: ['Anker', 'Sony'] });
    expect(body.brands).toEqual(['Anker', 'Sony']);
  });

  it('区间只填一端时另一端不传', () => {
    const body = toSearchBody(scope, { ...emptyFilters(), ranking: { min: 1000 } });
    expect(body.ranking_min).toBe(1000);
    expect(body.ranking_max).toBeUndefined();
  });

  it('份额按百分数原样传出，不做 0~1 换算', () => {
    const body = toSearchBody(scope, {
      ...emptyFilters(),
      clickShare: { min: 10, max: 20 },
      conversionShare: { min: 50 },
    });
    expect(body.click_share_min).toBe(10);
    expect(body.click_share_max).toBe(20);
    expect(body.conversion_share_min).toBe(50);
  });

  it('排名变化保留原始符号（负数 = 排名上升）', () => {
    const body = toSearchBody(scope, { ...emptyFilters(), change: { max: -1 } });
    expect(body.change_max).toBe(-1);
    expect(body.change_min).toBeUndefined();
  });

  it('新词三态：true / false 传出，undefined 不传', () => {
    expect(toSearchBody(scope, { ...emptyFilters(), isNew: true }).is_new).toBe(true);
    expect(toSearchBody(scope, { ...emptyFilters(), isNew: false }).is_new).toBe(false);
    expect(toSearchBody(scope, emptyFilters()).is_new).toBeUndefined();
  });
});

describe('快捷档位', () => {
  it('每组都有一个两端皆空的「全部」档', () => {
    for (const presets of Object.values(RANGE_PRESETS)) {
      const all = presets.find((p) => p.label === '全部');
      expect(all).toBeDefined();
      expect(all!.value).toEqual({});
    }
  });

  it('档位映射到具体上下界', () => {
    expect(RANGE_PRESETS.ranking.find((p) => p.label === '1~100')!.value).toEqual({
      min: 1,
      max: 100,
    });
    expect(RANGE_PRESETS.ranking.find((p) => p.label === '10万以上')!.value).toEqual({
      min: 100000,
    });
    expect(RANGE_PRESETS.change.find((p) => p.label === '上升')!.value).toEqual({ max: -1 });
    expect(RANGE_PRESETS.change.find((p) => p.label === '持平')!.value).toEqual({ min: 0, max: 0 });
  });

  it('区间等于某档位时该档位高亮', () => {
    expect(matchedPreset({ min: 1, max: 100 }, RANGE_PRESETS.ranking)).toBe('1~100');
    expect(matchedPreset({}, RANGE_PRESETS.ranking)).toBe('全部');
  });

  it('手输改写一端后不再匹配任何档位', () => {
    expect(matchedPreset({ min: 1, max: 99 }, RANGE_PRESETS.ranking)).toBeUndefined();
  });
});
