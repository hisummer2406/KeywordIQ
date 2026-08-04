import { describe, expect, it } from 'vitest';
import { productImageUrl } from './productImage';

describe('productImageUrl', () => {
  it('地址里带 .01. 段——少了它尺寸指令失效，返回的是全尺寸原图', () => {
    // 实测：省略该段时 _SL112_ 不被解析，同一张图 12.7KB vs 1.5KB
    const url = productImageUrl('B0TEST0001');
    expect(url).toBe('https://m.media-amazon.com/images/P/B0TEST0001.01._SL112_.jpg');
  });

  it('地址不随站点变化——图床按 ASIN 全局解析，市场码取值被忽略', () => {
    // 这条约束的意义是防止有人"补上"按站点分支的逻辑：实测三种码返回完全相同的字节，
    // 分支不产生任何效果，却会让未登记的站点直接不出图
    expect(productImageUrl('B0TEST0001')).not.toContain('images-eu');
    expect(productImageUrl('B0TEST0001')).not.toContain('images-na');
  });

  it('ASIN 两端空白被忽略', () => {
    expect(productImageUrl(' B0TEST0001 ')).toEqual(productImageUrl('B0TEST0001'));
  });

  it('ASIN 为空时返回 null，不拼出畸形地址', () => {
    // 返回 null 让调用方直接渲染占位，而不是拼个地址再等它返回透明像素
    expect(productImageUrl(null)).toBeNull();
    expect(productImageUrl(undefined)).toBeNull();
    expect(productImageUrl('')).toBeNull();
    expect(productImageUrl('   ')).toBeNull();
  });
});
