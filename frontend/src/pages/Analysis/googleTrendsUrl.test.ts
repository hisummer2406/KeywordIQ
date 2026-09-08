import { describe, expect, it } from 'vitest';
import { googleTrendsUrl } from './googleTrendsUrl';

describe('googleTrendsUrl', () => {
  it('站点码直接当 geo 参数用，不做映射', () => {
    expect(googleTrendsUrl('power bank', 'US')).toBe(
      'https://trends.google.com/trends/explore?q=power+bank&geo=US&date=today+12-m',
    );
    expect(googleTrendsUrl('wasserkocher', 'DE')).toBe(
      'https://trends.google.com/trends/explore?q=wasserkocher&geo=DE&date=today+12-m',
    );
  });

  it('站点码不分大小写，统一转大写', () => {
    expect(googleTrendsUrl('power bank', 'gb')).toContain('geo=GB');
  });

  it('对关键词做 URL 编码', () => {
    expect(googleTrendsUrl('salt & pepper', 'US')).toContain(encodeURIComponent('salt & pepper').replace(/%20/g, '+'));
    expect(googleTrendsUrl('咖啡机', 'US')).toContain(encodeURIComponent('咖啡机'));
  });

  it('站点码缺失时返回 null，由调用方降级为不渲染入口', () => {
    expect(googleTrendsUrl('power bank', undefined)).toBeNull();
    expect(googleTrendsUrl('power bank', '')).toBeNull();
  });

  it('关键词为空时返回 null', () => {
    expect(googleTrendsUrl('', 'US')).toBeNull();
    expect(googleTrendsUrl('   ', 'US')).toBeNull();
  });
});
