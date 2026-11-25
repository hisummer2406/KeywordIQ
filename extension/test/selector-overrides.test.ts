// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { detectBlockedPage, parseDeliveryText } from '../src/auto-plan';
import { fetchCollectorConfig } from '../src/collector-config';
import { hasNextPage, parseSellerName, parseStorefrontPage } from '../src/parse-storefront';
import { normalizeOverrides, usableSelectors } from '../src/selector-overrides';

function doc(html: string): Document {
  return new DOMParser().parseFromString(`<html><body>${html}</body></html>`, 'text/html');
}

describe('normalizeOverrides（extension-remote-selectors）', () => {
  it('合法形状原样保留', () => {
    expect(normalizeOverrides({ result_card: ['.card'], blocked_text: ['go away'] })).toEqual({
      result_card: ['.card'],
      blocked_text: ['go away'],
    });
  });

  it.each([
    ['未知键', { evil: ['x'] }],
    ['不是数组', { next_page: 'a.next' }],
    ['空数组', { next_page: [] }],
    ['超过 10 项', { next_page: Array(11).fill('a') }],
    ['超长', { next_page: ['x'.repeat(201)] }],
    ['空白项', { next_page: ['  '] }],
    ['非字符串', { next_page: [1] }],
    ['null', null],
    ['数组', [['a']]],
  ])('%s → 整体丢弃', (_label, raw) => {
    expect(normalizeOverrides(raw)).toBeUndefined();
  });
});

describe('usableSelectors', () => {
  it('下发值在前、内置在后、去重，跳过 CSS 语法不合法的', () => {
    const d = doc('<div></div>');
    expect(usableSelectors(['.new', '[[bad', '.old'], ['.old'], d)).toEqual(['.new', '.old']);
    expect(usableSelectors(undefined, ['.old'], d)).toEqual(['.old']);
  });
});

describe('解析函数使用覆盖', () => {
  const newCard = '<div class="new-card" data-asin="B0NEW00001"><h2><span>New</span></h2></div>';

  it('内置商品卡片选择器匹配不到时，用下发的匹配到', () => {
    const d = doc(newCard);
    expect(parseStorefrontPage(d)).toEqual([]);
    expect(parseStorefrontPage(d, ['.new-card']).map((i) => i.asin)).toEqual(['B0NEW00001']);
  });

  it('下发的选择器写错或匹配不到时回落内置', () => {
    const d = doc('<div data-component-type="s-search-result" data-asin="B0OLD00001"></div>');
    expect(parseStorefrontPage(d, ['[[bad', '.nothing']).map((i) => i.asin)).toEqual(['B0OLD00001']);
  });

  it('下一页、店名、配送地址都先用下发值', () => {
    const d = doc('<a class="next2">next</a><b class="name2">Acme</b><span id="loc2">New York 10001</span>');
    expect(hasNextPage(d)).toBe(false);
    expect(hasNextPage(d, ['a.next2'])).toBe(true);
    expect(parseSellerName(d, ['.name2'])).toBe('Acme');
    expect(parseDeliveryText(d)).toBeUndefined();
    expect(parseDeliveryText(d, ['#loc2'])).toBe('New York 10001');
  });

  it('拦截：下发选择器与纯文本文案（不当正则）', () => {
    expect(detectBlockedPage(doc('<div id="wall"></div>'), { selectors: ['#wall'] })).toBe(true);
    expect(detectBlockedPage(doc('<p>Please Slow Down</p>'), { texts: ['please slow down'] })).toBe(true);
    // ".*" 若被当成正则会匹配任何页面；纯文本只匹配字面量
    expect(detectBlockedPage(doc('<p>normal storefront</p>'), { texts: ['.*'] })).toBe(false);
  });
});

describe('fetchCollectorConfig 读取覆盖', () => {
  const respond = (body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })) as unknown as typeof fetch;

  it('合法覆盖与版本一并返回', async () => {
    const fetchImpl = respond({ max_report_items: 1000, selectors: { next_page: ['a.n'] }, selectors_version: 'v1' });
    expect(await fetchCollectorConfig('http://x', 't', { fetchImpl })).toEqual({
      kind: 'ok',
      maxReportItems: 1000,
      selectors: { next_page: ['a.n'] },
      selectorsVersion: 'v1',
    });
  });

  it('覆盖形状不对或缺版本：忽略覆盖，上限照常', async () => {
    for (const body of [
      { max_report_items: 1000, selectors: { evil: ['x'] }, selectors_version: 'v1' },
      { max_report_items: 1000, selectors: { next_page: ['a.n'] }, selectors_version: null },
      { max_report_items: 1000, selectors: null, selectors_version: null },
    ]) {
      expect(await fetchCollectorConfig('http://x', 't', { fetchImpl: respond(body) })).toEqual({
        kind: 'ok',
        maxReportItems: 1000,
      });
    }
  });
});
