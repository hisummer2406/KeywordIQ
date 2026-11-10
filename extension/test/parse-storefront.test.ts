// @vitest-environment jsdom
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { hasNextPage, parseSellerName, parseStorefrontPage } from '../src/parse-storefront';

const fixtureDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');

function loadFixture(name: string): Document {
  const html = readFileSync(path.join(fixtureDir, name), 'utf-8');
  return new DOMParser().parseFromString(html, 'text/html');
}

describe('parseStorefrontPage', () => {
  it('解析出全部合法商品卡片，跳过没有 data-asin 的卡片（广告位）', () => {
    const doc = loadFixture('storefront-sample.html');
    const items = parseStorefrontPage(doc);

    expect(items).toHaveLength(3);
    expect(items.map((i) => i.asin)).toEqual(['B0TESTASIN1', 'B0TESTASIN2', 'B0TESTASIN3']);
  });

  it('ASIN 统一大写', () => {
    const doc = loadFixture('storefront-sample.html');
    const items = parseStorefrontPage(doc);
    // fixture 里第二条是小写 b0testasin2
    expect(items[1]?.asin).toBe('B0TESTASIN2');
  });

  it('标题去除首尾空白，缺失时为 undefined', () => {
    const doc = loadFixture('storefront-sample.html');
    const items = parseStorefrontPage(doc);

    expect(items[0]?.title).toBe('Sample Product One');
    expect(items[2]?.title).toBe('Sample Product Three (needs trim)');
  });

  it('主图缺失时为 undefined，不是空字符串', () => {
    const doc = loadFixture('storefront-sample.html');
    const items = parseStorefrontPage(doc);

    expect(items[1]?.imageUrl).toBeUndefined();
    expect(items[0]?.imageUrl).toBe('https://m.media-amazon.com/images/I/sample1.jpg');
  });

  it('空页面返回空数组，不抛错', () => {
    const doc = new DOMParser().parseFromString('<html><body></body></html>', 'text/html');
    expect(parseStorefrontPage(doc)).toEqual([]);
  });
});

describe('hasNextPage', () => {
  it('存在未禁用的下一页链接时返回 true', () => {
    const doc = loadFixture('storefront-sample.html');
    expect(hasNextPage(doc)).toBe(true);
  });

  it('没有分页控件时返回 false', () => {
    const doc = new DOMParser().parseFromString('<html><body></body></html>', 'text/html');
    expect(hasNextPage(doc)).toBe(false);
  });

  it('下一页链接被禁用时返回 false（已是最后一页）', () => {
    const doc = new DOMParser().parseFromString(
      '<a class="s-pagination-next s-pagination-disabled">Next</a>',
      'text/html',
    );
    expect(hasNextPage(doc)).toBe(false);
  });
});

describe('parseSellerName（#166 D5，选择器待真实页面验收）', () => {
  function doc(html: string): Document {
    return new DOMParser().parseFromString(html, 'text/html');
  }

  it('找到店名元素时返回压缩空白后的文本', () => {
    expect(parseSellerName(doc('<div id="sellerName">  Acme \n Store </div>'))).toBe('Acme Store');
  });

  it('页面上没有店名元素时返回 undefined，不拼凑', () => {
    expect(parseSellerName(doc('<title>Amazon.com : A1TZ946F8HZTNY</title><div>x</div>'))).toBeUndefined();
  });

  it('超长文本不当作店名（多半是取错了元素）', () => {
    expect(parseSellerName(doc(`<div id="sellerName">${'x'.repeat(300)}</div>`))).toBeUndefined();
  });
});
