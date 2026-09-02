import { describe, expect, it } from 'vitest';
import {
  RANGE_PRESETS,
  detectSearchKind,
  defaultFilters,
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
    const asAsin = toSearchBody(scope, { ...defaultFilters(), q: 'B07Z82895W' });
    expect(asAsin.asin).toBe('B07Z82895W');
    expect(asAsin.keyword).toBeUndefined();

    const asKeyword = toSearchBody(scope, { ...defaultFilters(), q: 'screen protector' });
    expect(asKeyword.keyword).toBe('screen protector');
    expect(asKeyword.asin).toBeUndefined();
  });

  it('空条件只带作用域，其余字段一律 undefined', () => {
    // 这里要的是真正的中性值，故显式关掉默认的新词筛选（见 defaultFilters 的注释）。
    const body = toSearchBody(scope, { ...defaultFilters(), isNew: undefined });
    expect(body.site).toBe('US');
    expect(body.view).toBe('DAY');
    expect(body.date).toBe('2026-07-18');
    const rest = Object.entries(body).filter(([k]) => !['site', 'view', 'date'].includes(k));
    expect(rest.every(([, v]) => v === undefined)).toBe(true);
  });

  it('空类目数组不传——空集合视同不筛，传 [] 会让后端把它当成带筛选', () => {
    expect(toSearchBody(scope, defaultFilters()).categories).toBeUndefined();
  });

  it('非空类目原样传出', () => {
    const body = toSearchBody(scope, {
      ...defaultFilters(),
      categories: ['Electronics', 'Pet Supplies'],
    });
    expect(body.categories).toEqual(['Electronics', 'Pet Supplies']);
  });

  it('文本条件裁掉纯空白', () => {
    const body = toSearchBody(scope, { ...defaultFilters(), productTitle: ' ACME ' });
    expect(body.product_title).toBe('ACME');
  });

  it('品牌是多选集合，空集合不传（与类目对称）', () => {
    expect(toSearchBody(scope, defaultFilters()).brands).toBeUndefined();
    const body = toSearchBody(scope, { ...defaultFilters(), brands: ['Anker', 'Sony'] });
    expect(body.brands).toEqual(['Anker', 'Sony']);
  });

  it('区间只填一端时另一端不传', () => {
    const body = toSearchBody(scope, { ...defaultFilters(), ranking: { min: 1000 } });
    expect(body.ranking_min).toBe(1000);
    expect(body.ranking_max).toBeUndefined();
  });

  it('份额按百分数原样传出，不做 0~1 换算', () => {
    const body = toSearchBody(scope, {
      ...defaultFilters(),
      clickShare: { min: 10, max: 20 },
      conversionShare: { min: 50 },
    });
    expect(body.click_share_min).toBe(10);
    expect(body.click_share_max).toBe(20);
    expect(body.conversion_share_min).toBe(50);
  });

  it('排名变化保留原始符号（负数 = 排名上升）', () => {
    const body = toSearchBody(scope, { ...defaultFilters(), change: { max: -1 } });
    expect(body.change_max).toBe(-1);
    expect(body.change_min).toBeUndefined();
  });

  it('新词三态：true / false 传出，undefined 不传', () => {
    // 三态都显式传值，不依赖默认值恰好是其中某一个——默认值改动时这条不该跟着变。
    expect(toSearchBody(scope, { ...defaultFilters(), isNew: true }).is_new).toBe(true);
    expect(toSearchBody(scope, { ...defaultFilters(), isNew: false }).is_new).toBe(false);
    expect(toSearchBody(scope, { ...defaultFilters(), isNew: undefined }).is_new).toBeUndefined();
  });

  it('竞对新品是两态开关：勾选时传 true，不勾选不传（issue #56 P2）', () => {
    // 与新词不同，这里没有第三态——不勾选就是「不筛」，不需要区分「未勾选」与「明确要全部」。
    expect(toSearchBody(scope, { ...defaultFilters(), competitorNew: true }).competitor_new).toBe(
      true,
    );
    expect(
      toSearchBody(scope, { ...defaultFilters(), competitorNew: false }).competitor_new,
    ).toBeUndefined();
  });
});

describe('defaultFilters', () => {
  it('默认只看新词', () => {
    expect(defaultFilters().isNew).toBe(true);
    expect(toSearchBody(scope, defaultFilters()).is_new).toBe(true);
  });

  it('除新词外其余维度默认都不筛', () => {
    const body = toSearchBody(scope, defaultFilters());
    const rest = Object.entries(body).filter(
      ([k]) => !['site', 'view', 'date', 'is_new'].includes(k),
    );
    expect(rest.every(([, v]) => v === undefined)).toBe(true);
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

// --------------------------------------------------------------------------
// 行集合开关（include）
// --------------------------------------------------------------------------

describe('行集合开关', () => {
  it('缺省不传 include —— 后端缺省即 ALL，不必显式带上', () => {
    const body = toSearchBody(scope, defaultFilters());
    expect(body.include).toBeUndefined();
  });

  it('选定 LISTED / DROPPED 时透传', () => {
    expect(toSearchBody(scope, { ...defaultFilters(), include: 'LISTED' }).include).toBe('LISTED');
    expect(toSearchBody(scope, { ...defaultFilters(), include: 'DROPPED' }).include).toBe('DROPPED');
  });

  it('仅掉榜时不带任何其余筛选——它们只会让掉榜段恒为空', () => {
    // 筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行可匹配。带上去的结果是
    // 用户选了「仅掉榜」又填了条件，然后得到一张空表，且看不出是哪一步造成的。
    const body = toSearchBody(scope, {
      ...defaultFilters(),
      include: 'DROPPED',
      q: 'coffee',
      brands: ['Anker'],
      categories: ['Electronics'],
      productTitle: 'maker',
      ranking: { min: 1, max: 100 },
      change: { min: -50 },
      clickShare: { min: 10 },
      conversionShare: { min: 5 },
      isNew: true,
      competitorNew: true,
    });
    expect(body.include).toBe('DROPPED');
    expect(body.keyword).toBeUndefined();
    expect(body.asin).toBeUndefined();
    expect(body.brands).toBeUndefined();
    expect(body.categories).toBeUndefined();
    expect(body.product_title).toBeUndefined();
    expect(body.ranking_min).toBeUndefined();
    expect(body.ranking_max).toBeUndefined();
    expect(body.change_min).toBeUndefined();
    expect(body.click_share_min).toBeUndefined();
    expect(body.conversion_share_min).toBeUndefined();
    expect(body.is_new).toBeUndefined();
    expect(body.competitor_new).toBeUndefined();
  });

  it('ALL / LISTED 下其余筛选照常带上', () => {
    const body = toSearchBody(scope, {
      ...defaultFilters(),
      include: 'LISTED',
      q: 'coffee',
      ranking: { min: 1, max: 100 },
      competitorNew: true,
    });
    expect(body.keyword).toBe('coffee');
    expect(body.ranking_min).toBe(1);
    expect(body.competitor_new).toBe(true);
  });

});
