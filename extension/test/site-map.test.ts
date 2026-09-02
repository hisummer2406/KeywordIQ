import { describe, expect, it } from 'vitest';
import { DOMAIN_TO_SITE, MARKETPLACE_ID_TO_SITE, resolveSite } from '../src/site-map';

describe('resolveSite', () => {
  it('优先按 marketplaceID 识别（三个已知站点）', () => {
    expect(resolveSite('https://www.amazon.com/s?i=merchant-items&me=X&marketplaceID=ATVPDKIKX0DER')).toBe('US');
    expect(resolveSite('https://www.amazon.co.uk/s?i=merchant-items&me=X&marketplaceID=A1F83G8C2ARO7')).toBe('GB');
    expect(resolveSite('https://www.amazon.de/s?i=merchant-items&me=X&marketplaceID=A1PA6795UKMFR9')).toBe('DE');
  });

  it('marketplaceID 缺失时回落域名（三个已知站点）', () => {
    expect(resolveSite('https://www.amazon.com/s?i=merchant-items&me=X')).toBe('US');
    expect(resolveSite('https://www.amazon.co.uk/s?i=merchant-items&me=X')).toBe('GB');
    expect(resolveSite('https://www.amazon.de/s?i=merchant-items&me=X')).toBe('DE');
  });

  it('marketplaceID 存在但未登记时仍回落域名', () => {
    expect(resolveSite('https://www.amazon.com/s?me=X&marketplaceID=UNKNOWN_ID')).toBe('US');
  });

  it('两者都无法识别时返回 null，不猜测默认站点', () => {
    expect(resolveSite('https://www.amazon.fr/s?i=merchant-items&me=X')).toBeNull();
  });

  it('URL 非法时返回 null', () => {
    expect(resolveSite('not a url')).toBeNull();
  });

  it('两张映射表的站点集合与已知站点一致（防漂移，非自动同步）', () => {
    // 这条测试的意义不是自动同步 app/site_registry.py——那需要跨语言读取 Python
    // 源码，这里做不到。它的意义是"改动这份镜像时至少会被自己的测试看见"
    // （design.md 决策 2）：改错了会看到断言失败，而不是悄悄产生第三个站点集合。
    const known = ['US', 'GB', 'DE'];
    expect(new Set(Object.values(MARKETPLACE_ID_TO_SITE))).toEqual(new Set(known));
    expect(new Set(Object.values(DOMAIN_TO_SITE))).toEqual(new Set(known));
  });
});
