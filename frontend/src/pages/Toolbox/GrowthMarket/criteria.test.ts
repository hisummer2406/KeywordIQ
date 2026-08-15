import { describe, expect, it } from 'vitest';
import { formatCriteria, formatMissingDates, formatPercent, toSortParams } from './criteria';

describe('formatCriteria', () => {
  it('三个数值全部取自服务端回传，不写死', () => {
    const text = formatCriteria({ window_days: 7, min_rank_gain: 10000, min_daily_rate: 0.1 });

    expect(text).toContain('近 7 天');
    expect(text).toContain('10,000 名');
    expect(text).toContain('10%');
  });

  it('后端改了阈值，文案跟着改', () => {
    const text = formatCriteria({ window_days: 14, min_rank_gain: 5000, min_daily_rate: 0.05 });

    expect(text).toContain('近 14 天');
    expect(text).toContain('5,000 名');
    expect(text).toContain('5%');
  });
});

describe('formatPercent', () => {
  it('整数百分比不留小数尾巴', () => {
    expect(formatPercent(0.1)).toBe('10%');
  });

  it('非整数保留两位', () => {
    expect(formatPercent(0.1234)).toBe('12.34%');
  });
});

describe('formatMissingDates', () => {
  it('窗口完整时返回 null，界面不显示提示条', () => {
    expect(formatMissingDates([])).toBeNull();
  });

  it('缺期时列出具体日期，并说明这不是「没有增长词」', () => {
    const text = formatMissingDates(['2026-08-11', '2026-08-12']);

    expect(text).toContain('2026-08-11、2026-08-12');
    expect(text).toContain('共 2 期');
    // 这半句是这条提示存在的全部理由：两种情况在界面上长得一样
    expect(text).toContain('不是「今天没有增长词」');
  });
});

describe('toSortParams', () => {
  it('无排序时按累计前进降序', () => {
    expect(toSortParams(undefined)).toEqual({ orderBy: 'rank_gain', orderDir: 'desc' });
    expect(toSortParams({})).toEqual({ orderBy: 'rank_gain', orderDir: 'desc' });
  });

  it('升序降序都能转', () => {
    expect(toSortParams({ min_growth_rate: 'ascend' })).toEqual({
      orderBy: 'min_growth_rate',
      orderDir: 'asc',
    });
    expect(toSortParams({ end_ranking: 'descend' })).toEqual({
      orderBy: 'end_ranking',
      orderDir: 'desc',
    });
  });

  it('白名单外的字段回落默认，不把 422 送到用户面前', () => {
    expect(toSortParams({ keyword: 'ascend' })).toEqual({
      orderBy: 'rank_gain',
      orderDir: 'desc',
    });
  });

  it('取消排序（order 为 null）视同无排序', () => {
    expect(toSortParams({ rank_gain: null as unknown as string })).toEqual({
      orderBy: 'rank_gain',
      orderDir: 'desc',
    });
  });
});
