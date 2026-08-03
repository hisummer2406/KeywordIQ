import { describe, expect, it } from 'vitest';
import { formatScopeDateLabel } from './scopeDateLabel';

describe('formatScopeDateLabel', () => {
  it('日视图显示中文年月日', () => {
    expect(formatScopeDateLabel('2026-07-18', 'DAY')).toBe('2026年07月18日');
  });

  it('周视图显示周序号及周日至周六的日期范围', () => {
    expect(formatScopeDateLabel('2026-07-23', 'WEEK')).toBe('2026年第30周（07/19–07/25）');
  });

  it('无法识别的日期保留原文', () => {
    expect(formatScopeDateLabel('unknown', 'WEEK')).toBe('unknown');
  });
});
