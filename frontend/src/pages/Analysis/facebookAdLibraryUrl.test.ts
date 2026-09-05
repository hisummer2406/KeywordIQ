import { describe, expect, it } from 'vitest';
import { facebookAdLibraryUrl } from './facebookAdLibraryUrl';

describe('facebookAdLibraryUrl', () => {
  it('站点码直接当 country 参数用，不做映射', () => {
    expect(facebookAdLibraryUrl('power bank', 'US')).toBe(
      'https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=power+bank&search_type=keyword_unordered',
    );
  });

  it('站点码不分大小写，统一转大写', () => {
    expect(facebookAdLibraryUrl('power bank', 'gb')).toContain('country=GB');
  });

  it('对关键词做 URL 编码', () => {
    expect(facebookAdLibraryUrl('salt & pepper', 'US')).toContain(
      encodeURIComponent('salt & pepper').replace(/%20/g, '+'),
    );
  });

  it('站点码缺失时返回 null，由调用方降级为不渲染入口', () => {
    expect(facebookAdLibraryUrl('power bank', undefined)).toBeNull();
    expect(facebookAdLibraryUrl('power bank', '')).toBeNull();
  });

  it('关键词为空时返回 null', () => {
    expect(facebookAdLibraryUrl('', 'US')).toBeNull();
    expect(facebookAdLibraryUrl('   ', 'US')).toBeNull();
  });
});
