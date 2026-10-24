import { describe, expect, it } from 'vitest';
import {
  MAX_ASINS,
  RANGE_PRESETS,
  defaultFilters,
  matchedPreset,
  sanitizeCompetitorNew,
  searchInputError,
  splitAsins,
  toSearchBody,
  type ScopeDraft,
} from './filters';

const scope: ScopeDraft = { site: 'US', view: 'DAY', date: '2026-07-18' };

describe('splitAsins', () => {
  it('空格、逗号、中文逗号、分号、换行与制表符都是分隔符', () => {
    expect(splitAsins('B0AAAAAAA1 B0BBBBBBB2,B0CCCCCCC3，B0DDDDDDD4;B0EEEEEEE5；B0FFFFFFF6')).toEqual([
      'B0AAAAAAA1',
      'B0BBBBBBB2',
      'B0CCCCCCC3',
      'B0DDDDDDD4',
      'B0EEEEEEE5',
      'B0FFFFFFF6',
    ]);
    // Excel 多行多单元格：\r\n 分行、\t 分列
    expect(splitAsins('B0AAAAAAA1\tB0BBBBBBB2\r\nB0CCCCCCC3\n')).toEqual([
      'B0AAAAAAA1',
      'B0BBBBBBB2',
      'B0CCCCCCC3',
    ]);
  });

  it('忽略空项与首尾空白，大小写不敏感地去重并保序', () => {
    expect(splitAsins('  b0aaaaaaa1 ,, B0AAAAAAA1 ;  b0bbbbbbb2  ')).toEqual(['B0AAAAAAA1', 'B0BBBBBBB2']);
    expect(splitAsins('   ')).toEqual([]);
  });

  it('不按 B0 格式过滤：ISBN-10 等编号原样保留', () => {
    expect(splitAsins('3161484100 B0AAAAAAA1')).toEqual(['3161484100', 'B0AAAAAAA1']);
  });
});

describe('searchInputError', () => {
  const asins = (n: number) => Array.from({ length: n }, (_, i) => `B0X${String(i).padStart(7, '0')}`);

  it('去重后恰好 100 个可提交，101 个给出原因', () => {
    expect(searchInputError(asins(MAX_ASINS).join(' '), 'ASIN')).toBeUndefined();
    // 重复项不计数
    expect(searchInputError([...asins(MAX_ASINS), ...asins(MAX_ASINS)].join(','), 'ASIN')).toBeUndefined();
    expect(searchInputError(asins(MAX_ASINS + 1).join(' '), 'ASIN')).toMatch(/最多 100 个，当前 101 个/);
  });

  it('关键词方式不做 ASIN 计数', () => {
    expect(searchInputError(asins(MAX_ASINS + 1).join(' '), 'ALL_TERMS')).toBeUndefined();
  });
});

describe('toSearchBody', () => {
  it('检索目标只由搜索方式决定，另一个不传', () => {
    const asAsin = toSearchBody(scope, { ...defaultFilters(), q: 'b07z82895w, 3161484100', qMode: 'ASIN' });
    expect(asAsin.asins).toEqual(['B07Z82895W', '3161484100']);
    expect(asAsin.keyword).toBeUndefined();
    expect(asAsin.keyword_mode).toBeUndefined();

    // 形如 ASIN 的输入在关键词方式下仍按关键词查——不再按格式推断
    const asKeyword = toSearchBody(scope, { ...defaultFilters(), q: 'B07Z82895W' });
    expect(asKeyword.keyword).toBe('B07Z82895W');
    expect(asKeyword.keyword_mode).toBe('ALL_TERMS');
    expect(asKeyword.asins).toBeUndefined();

    // 10 位英文词不会被当成 ASIN
    const word = toSearchBody(scope, { ...defaultFilters(), q: 'headphones', qMode: 'PHRASE' });
    expect(word).toMatchObject({ keyword: 'headphones', keyword_mode: 'PHRASE' });
    expect(word.asins).toBeUndefined();
  });

  it('ASIN 方式下空输入不带任何检索条件', () => {
    const body = toSearchBody(scope, { ...defaultFilters(), q: ' ,; ', qMode: 'ASIN' });
    expect(body.asins).toBeUndefined();
    expect(body.keyword).toBeUndefined();
  });

  it('空条件只带作用域，其余字段一律 undefined', () => {
    // 这里要的是真正的中性值，故显式关掉默认的首次入榜筛选（见 defaultFilters 的注释）。
    const body = toSearchBody(scope, { ...defaultFilters(), firstListed: undefined });
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

  it('首次入榜两态：true 传出，undefined 不传', () => {
    // 两态都显式传值，不依赖默认值恰好是其中某一个——默认值改动时这条不该跟着变。
    expect(toSearchBody(scope, { ...defaultFilters(), firstListed: true }).first_listed).toBe(true);
    expect(toSearchBody(scope, { ...defaultFilters(), firstListed: undefined }).first_listed).toBeUndefined();
  });

  it('竞对新发现是两态开关：勾选时传 true，不勾选不传（issue #56 P2）', () => {
    // 与首次入榜一样，这里没有第三态——不勾选就是「不筛」，不需要区分「未勾选」与「明确要全部」。
    expect(toSearchBody(scope, { ...defaultFilters(), competitorNew: true }).competitor_new).toBe(
      true,
    );
    expect(
      toSearchBody(scope, { ...defaultFilters(), competitorNew: false }).competitor_new,
    ).toBeUndefined();
  });
});

describe('sanitizeCompetitorNew', () => {
  it('有权限时原样透传', () => {
    const filters = { ...defaultFilters(), competitorNew: true };
    expect(sanitizeCompetitorNew(filters, true)).toEqual(filters);
  });

  it('无权限时强制关闭，即便原值是 true（PR #66 复审 P2）', () => {
    // 保存的条件跨登录持久：用户保存时有权限、之后被收回，原样恢复会让首屏自动查询
    // 带着 competitor_new=true 撞上后端权限校验（403），而开关又因无权限被隐藏，
    // 用户看不到也关不掉这个仍在生效的条件。
    const filters = { ...defaultFilters(), competitorNew: true };
    expect(sanitizeCompetitorNew(filters, false).competitorNew).toBe(false);
  });

  it('无权限且原值已是 false 时不受影响', () => {
    const filters = { ...defaultFilters(), competitorNew: false };
    expect(sanitizeCompetitorNew(filters, false)).toEqual(filters);
  });
});

describe('defaultFilters', () => {
  it('默认只看首次入榜', () => {
    expect(defaultFilters().firstListed).toBe(true);
    expect(toSearchBody(scope, defaultFilters()).first_listed).toBe(true);
  });

  it('除首次入榜与排除品牌词外其余维度默认都不筛', () => {
    const body = toSearchBody(scope, defaultFilters());
    const rest = Object.entries(body).filter(
      ([k]) => !['site', 'view', 'date', 'first_listed', 'exclude_brand_in_keyword'].includes(k),
    );
    expect(rest.every(([, v]) => v === undefined)).toBe(true);
  });

  it('排除品牌词默认开启，但只在「只看首次入榜」时随请求发出（issue #119）', () => {
    expect(toSearchBody(scope, defaultFilters()).exclude_brand_in_keyword).toBe(true);
    // 看全部：不替用户缩减榜单
    for (const firstListed of [undefined]) {
      expect(
        toSearchBody(scope, { ...defaultFilters(), firstListed }).exclude_brand_in_keyword,
      ).toBeUndefined();
    }
    // 用户关掉、旧快照缺字段（视同开启）、仅掉榜
    expect(
      toSearchBody(scope, { ...defaultFilters(), excludeBrandWords: false }).exclude_brand_in_keyword,
    ).toBeUndefined();
    expect(
      toSearchBody(scope, { ...defaultFilters(), excludeBrandWords: undefined })
        .exclude_brand_in_keyword,
    ).toBe(true);
    expect(
      toSearchBody(scope, { ...defaultFilters(), include: 'DROPPED' }).exclude_brand_in_keyword,
    ).toBeUndefined();
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
    expect(RANGE_PRESETS.change.find((p) => p.label === '上升')!.value).toEqual({
      min: -149999,
      max: -1,
    });
    expect(RANGE_PRESETS.change.find((p) => p.label === '大幅上升')!.value).toEqual({
      max: -150000,
    });
  });

  it('排名变化只有全部/上升/大幅上升三档，且边界 150000 只归大幅上升', () => {
    expect(RANGE_PRESETS.change.map((p) => p.label)).toEqual(['全部', '上升', '大幅上升']);
    const inRange = (v: number, r: { min?: number; max?: number }) =>
      (r.min === undefined || v >= r.min) && (r.max === undefined || v <= r.max);
    const up = RANGE_PRESETS.change[1].value;
    const big = RANGE_PRESETS.change[2].value;
    expect([-1, -149999].map((v) => inRange(v, up))).toEqual([true, true]);
    expect([-1, -149999].map((v) => inRange(v, big))).toEqual([false, false]);
    expect([-150000, -300000].map((v) => inRange(v, big))).toEqual([true, true]);
    expect([-150000, -300000].map((v) => inRange(v, up))).toEqual([false, false]);
    for (const v of [0, 1, 500]) {
      expect(inRange(v, up) || inRange(v, big)).toBe(false);
    }
  });

  it('旧的已保存区间不会被静默映射成新档位', () => {
    expect(matchedPreset({ max: -100 }, RANGE_PRESETS.change)).toBeUndefined();
    expect(matchedPreset({ max: -1 }, RANGE_PRESETS.change)).toBeUndefined();
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
      firstListed: true,
      competitorNew: true,
    });
    expect(body.include).toBe('DROPPED');
    expect(body.keyword).toBeUndefined();
    expect(body.asins).toBeUndefined();
    expect(body.brands).toBeUndefined();
    expect(body.categories).toBeUndefined();
    expect(body.product_title).toBeUndefined();
    expect(body.ranking_min).toBeUndefined();
    expect(body.ranking_max).toBeUndefined();
    expect(body.change_min).toBeUndefined();
    expect(body.click_share_min).toBeUndefined();
    expect(body.conversion_share_min).toBeUndefined();
    expect(body.first_listed).toBeUndefined();
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

describe('关键词匹配方式 qMode', () => {
  const scope = { site: 'US', view: 'DAY' as const, date: '2026-07-18' };

  it('默认全部词；关键词输入时随请求带上 keyword_mode', () => {
    expect(defaultFilters().qMode).toBe('ALL_TERMS');
    const body = toSearchBody(scope, { ...defaultFilters(), q: 'faucet sink organizer' });
    expect(body.keyword).toBe('faucet sink organizer');
    expect(body.keyword_mode).toBe('ALL_TERMS');
  });

  it('选了连续词组就带 PHRASE；缺省（旧快照）视同全部词', () => {
    expect(toSearchBody(scope, { ...defaultFilters(), q: 'rug', qMode: 'PHRASE' }).keyword_mode).toBe(
      'PHRASE',
    );
    expect(toSearchBody(scope, { ...defaultFilters(), q: 'rug', qMode: undefined }).keyword_mode).toBe(
      'ALL_TERMS',
    );
  });

  it('ASIN、空输入、仅掉榜时不带 keyword_mode', () => {
    expect(toSearchBody(scope, { ...defaultFilters(), q: 'B07Z82895W', qMode: 'ASIN' }).keyword_mode).toBeUndefined();
    expect(toSearchBody(scope, { ...defaultFilters(), q: '' }).keyword_mode).toBeUndefined();
    expect(
      toSearchBody(scope, { ...defaultFilters(), q: 'rug', include: 'DROPPED' }).keyword_mode,
    ).toBeUndefined();
  });
});
