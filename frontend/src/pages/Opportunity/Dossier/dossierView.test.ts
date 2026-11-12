import { describe, expect, it } from 'vitest';
import type { ChangeRow } from '@/services/opportunityDossier';
import { describeChange, formatMoney, formatRate, isDue, parseKeywordInput, rankDelta } from './dossierView';

describe('rankDelta', () => {
  it('排名数字变小是上升', () => {
    expect(rankDelta(119869, 15746)).toEqual({ places: 104123, direction: 'up' });
    expect(rankDelta(100, 300)).toEqual({ places: 200, direction: 'down' });
    expect(rankDelta(5, 5)).toEqual({ places: 0, direction: 'flat' });
  });
  it('任一侧缺失不当成 0', () => {
    expect(rankDelta(null, 100)).toBeNull();
    expect(rankDelta(100, undefined)).toBeNull();
  });
});

describe('isDue', () => {
  it('复查日期不晚于今天即待复查', () => {
    expect(isDue('2026-11-12', '2026-11-12')).toBe(true);
    expect(isDue('2026-11-10', '2026-11-12')).toBe(true);
    expect(isDue('2026-11-13', '2026-11-12')).toBe(false);
    expect(isDue(null, '2026-11-12')).toBe(false);
  });
});

describe('parseKeywordInput', () => {
  it('按换行与中英文逗号拆分，规范化并去重', () => {
    expect(parseKeywordInput('Melty  Mug\nmeltzy mug， melty mug ,\n\n chocolate melting cup;')).toEqual([
      'melty mug',
      'meltzy mug',
      'chocolate melting cup',
    ]);
  });
});

describe('describeChange', () => {
  const side = (ranking: number | null, date: string, ranked: number, top1: string | null = 'A') => ({
    resolved: true,
    latest: ranking === null ? null : { date, ranking },
    ranked_periods: ranked,
    top1_asin: top1,
  });
  it('排名、在榜期数与点击第一的变化', () => {
    const row: ChangeRow = { keyword: 'k', state: 'kept', before: side(1000, '2026-11-10', 10), after: side(800, '2026-11-11', 11, 'B'), top1_changed: true };
    expect(describeChange(row)).toBe('排名 1,000 → 800（上升 200 名）；在榜期数 10 → 11；点击第一的商品换了');
  });
  it('数据期次没变时不说成持平', () => {
    const row: ChangeRow = { keyword: 'k', state: 'kept', before: side(1000, '2026-11-10', 10), after: side(1000, '2026-11-10', 10) };
    expect(describeChange(row)).toBe('最新在榜期未变（2026-11-10，排名 1,000）');
  });
  it('新增、移除与未进榜的词', () => {
    expect(describeChange({ keyword: 'k', state: 'added', before: null, after: side(1, 'd', 1) })).toBe('新加入的词');
    expect(describeChange({ keyword: 'k', state: 'removed', before: side(1, 'd', 1), after: null })).toBe('已从档案移除');
    const never = { resolved: false, latest: null, ranked_periods: null, top1_asin: null };
    expect(describeChange({ keyword: 'k', state: 'kept', before: never, after: never })).toBe('保留期内仍未进入过榜单');
  });
});

describe('formatMoney / formatRate（只展示核价引擎的原值，不计算）', () => {
  it('加千分位与币种符号，小数位原样保留', () => {
    expect(formatMoney('93.75', 'USD')).toBe('$93.75');
    expect(formatMoney('12345.6', 'CNY')).toBe('¥12,345.6');
    expect(formatMoney('-1234.00', 'CNY')).toBe('-¥1,234.00');
    expect(formatMoney('1000', 'JPY')).toBe('JPY 1,000');
  });
  it('空值不当成 0', () => {
    expect(formatMoney(null, 'USD')).toBe('—');
    expect(formatMoney('', 'USD')).toBe('—');
    expect(formatRate(null)).toBe('—');
  });
  it('利润率是 0~1 的小数', () => {
    expect(formatRate('0.4267')).toBe('42.67%');
  });
});
