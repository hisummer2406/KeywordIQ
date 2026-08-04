import { describe, expect, it } from 'vitest';
import { amazonSearchUrl } from './amazonSearchUrl';

describe('amazonSearchUrl', () => {
  it('按站点域名拼搜索结果页', () => {
    expect(amazonSearchUrl('power bank', 'amazon.com')).toBe(
      'https://www.amazon.com/s?k=power%20bank',
    );
    expect(amazonSearchUrl('wasserkocher', 'amazon.de')).toBe(
      'https://www.amazon.de/s?k=wasserkocher',
    );
  });

  it('对关键词做 URL 编码', () => {
    // & 不编码的话会被当成参数分隔符，跳过去只搜到半截词
    expect(amazonSearchUrl('salt & pepper', 'amazon.com')).toContain('salt%20%26%20pepper');
    // 中文与 emoji 同样要能过
    expect(amazonSearchUrl('咖啡机', 'amazon.com')).toContain(encodeURIComponent('咖啡机'));
  });

  it('域名缺失时返回 null，由调用方降级为纯文本', () => {
    // 站点列表还没加载完时会走到这里。宁可不给链接，也不要拼出一个 https://undefined/s?k=
    expect(amazonSearchUrl('power bank', undefined)).toBeNull();
    expect(amazonSearchUrl('power bank', '')).toBeNull();
  });

  it('关键词为空时返回 null', () => {
    expect(amazonSearchUrl('', 'amazon.com')).toBeNull();
    expect(amazonSearchUrl('   ', 'amazon.com')).toBeNull();
  });
});
