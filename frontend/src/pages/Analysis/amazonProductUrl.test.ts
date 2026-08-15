import { describe, expect, it } from 'vitest';
import { amazonProductUrl } from './amazonProductUrl';

describe('amazonProductUrl', () => {
  it('拼出该站点的商品详情页地址', () => {
    expect(amazonProductUrl('B07Z82895W', 'amazon.com')).toBe(
      'https://www.amazon.com/dp/B07Z82895W',
    );
  });

  it('域名随站点变化——GB 是 amazon.co.uk 不是 amazon.uk', () => {
    expect(amazonProductUrl('B07Z82895W', 'amazon.co.uk')).toBe(
      'https://www.amazon.co.uk/dp/B07Z82895W',
    );
  });

  it('拿不到域名时返回 null，不拼出 https://undefined/dp/...', () => {
    // 站点列表尚未加载完时就会走到这条路径；宁可不给链接，也不要给一个错误页面
    expect(amazonProductUrl('B07Z82895W', undefined)).toBeNull();
    expect(amazonProductUrl('B07Z82895W', '')).toBeNull();
  });

  it('ASIN 为空时返回 null', () => {
    expect(amazonProductUrl('', 'amazon.com')).toBeNull();
    expect(amazonProductUrl('   ', 'amazon.com')).toBeNull();
  });
});
