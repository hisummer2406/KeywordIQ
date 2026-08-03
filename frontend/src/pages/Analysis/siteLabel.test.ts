import { describe, expect, it } from 'vitest';
import { formatSiteLabel, siteCodeToFlag } from './siteLabel';

describe('siteCodeToFlag', () => {
  it.each([
    ['US', '🇺🇸'],
    ['GB', '🇬🇧'],
    ['DE', '🇩🇪'],
    [' us ', '🇺🇸'],
  ])('将 %s 转为 %s', (code, flag) => {
    expect(siteCodeToFlag(code)).toBe(flag);
  });

  it.each(['', 'U', 'USA', '1S'])('非法代码 %s 不生成国旗', (code) => {
    expect(siteCodeToFlag(code)).toBe('');
  });
});

describe('formatSiteLabel', () => {
  it('组合国旗、中文站点名和全角括号代码', () => {
    expect(formatSiteLabel({ code: 'US', name_zh: '美国', domain: 'amazon.com' })).toBe(
      '🇺🇸 美国站（US）',
    );
  });
});
