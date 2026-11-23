// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  buildStorefrontUrl,
  decideAfterPage,
  deliveryMatchesSite,
  detectBlockedPage,
  parseDeliveryText,
  pickStoreGapMs,
  STORE_GAP_MS,
} from '../src/auto-plan';
import { DEFAULT_AUTO_CONFIG, nextDailyRunAt, normalizeAutoConfig } from '../src/auto-config';

const item = (asin: string) => ({ asin });

describe('buildStorefrontUrl', () => {
  it('带 i=merchant-items 与按最新排序，第 1 页不带 page', () => {
    const url = new URL(buildStorefrontUrl('A2DM6QLA6GKDQ4', 'US', 1)!);
    expect(url.hostname).toBe('www.amazon.com');
    expect(url.searchParams.get('i')).toBe('merchant-items');
    expect(url.searchParams.get('me')).toBe('A2DM6QLA6GKDQ4');
    expect(url.searchParams.get('marketplaceID')).toBe('ATVPDKIKX0DER');
    expect(url.searchParams.get('s')).toBe('date-desc-rank');
    expect(url.searchParams.has('page')).toBe(false);
  });

  it('第 2 页带 page=2；GB 用 co.uk', () => {
    const url = new URL(buildStorefrontUrl('A1L5ZY4CAY0K3D', 'GB', 2)!);
    expect(url.hostname).toBe('www.amazon.co.uk');
    expect(url.searchParams.get('page')).toBe('2');
  });

  it('未登记的站点返回 null，不猜默认', () => {
    expect(buildStorefrontUrl('A1', 'JP', 1)).toBeNull();
  });
});

describe('pickStoreGapMs', () => {
  it('落在 30–60 秒之间', () => {
    expect(pickStoreGapMs(() => 0)).toBe(STORE_GAP_MS.min);
    expect(pickStoreGapMs(() => 0.9999)).toBeLessThan(STORE_GAP_MS.max);
  });
});

describe('detectBlockedPage', () => {
  it('识别验证码表单', () => {
    document.body.innerHTML = '<form action="/errors/validateCaptcha"><input id="captchacharacters"></form>';
    expect(detectBlockedPage(document)).toBe(true);
  });

  it('识别 Access restricted 文案', () => {
    document.body.innerHTML = '<h1>Access restricted</h1><p>unauthorized AI agent</p>';
    expect(detectBlockedPage(document)).toBe(true);
  });

  it('正常店铺页不算拦截', () => {
    document.body.innerHTML = '<div data-component-type="s-search-result" data-asin="B0TEST00001"><h2><span>Mug</span></h2></div>';
    expect(detectBlockedPage(document)).toBe(false);
  });
});

describe('decideAfterPage', () => {
  const base = { page: 1, maxPages: 2, accumulated: [], cap: 1000 };

  it('拦截页 → 整轮停止', () => {
    expect(decideAfterPage({ ...base, scan: { kind: 'blocked' } })).toEqual({ action: 'stop_run', reason: 'blocked' });
  });

  it('第 1 页为空 → 这家店失败，不上报', () => {
    expect(decideAfterPage({ ...base, scan: { kind: 'ok', items: [], hasNext: false } })).toEqual({
      action: 'fail_store',
      reason: 'empty_page',
    });
  });

  it('第 1 页有下一页且未到 N 页 → 继续翻页', () => {
    const decision = decideAfterPage({ ...base, scan: { kind: 'ok', items: [item('B01')], hasNext: true } });
    expect(decision).toEqual({ action: 'next_page', items: [item('B01')] });
  });

  it('没有下一页 → 直接上报', () => {
    const decision = decideAfterPage({ ...base, scan: { kind: 'ok', items: [item('B01')], hasNext: false } });
    expect(decision).toEqual({ action: 'report', items: [item('B01')] });
  });

  it('到达 N 页 → 合并去重后上报一次', () => {
    const decision = decideAfterPage({
      ...base,
      page: 2,
      accumulated: [item('B01'), item('B02')],
      scan: { kind: 'ok', items: [item('B02'), item('B03')], hasNext: true },
    });
    expect(decision).toEqual({ action: 'report', items: [item('B01'), item('B02'), item('B03')] });
  });

  it('第 2 页为空 → 用已合并的第 1 页结果上报', () => {
    const decision = decideAfterPage({
      ...base,
      page: 2,
      accumulated: [item('B01')],
      scan: { kind: 'ok', items: [], hasNext: false },
    });
    expect(decision).toEqual({ action: 'report', items: [item('B01')] });
  });

  it('超过单批上限 → 这家店失败，不截断', () => {
    const decision = decideAfterPage({
      ...base,
      cap: 2,
      accumulated: [item('B01'), item('B02')],
      page: 2,
      scan: { kind: 'ok', items: [item('B03')], hasNext: false },
    });
    expect(decision).toEqual({ action: 'fail_store', reason: 'over_cap' });
  });
});

describe('配送地址检查（试点 2026-11-24：配送到中国时店铺页大多 No results）', () => {
  it('读取页头配送地址', () => {
    document.body.innerHTML = '<span id="glow-ingress-line1">Deliver to</span><span id="glow-ingress-line2"> New York 10001 </span>';
    expect(parseDeliveryText(document)).toBe('New York 10001');
  });

  it('读不到时返回 undefined', () => {
    document.body.innerHTML = '<div></div>';
    expect(parseDeliveryText(document)).toBeUndefined();
  });

  it('US 邮编算相符，China 算不符，读不到算未知', () => {
    expect(deliveryMatchesSite('New York 10001', 'US')).toBe('match');
    expect(deliveryMatchesSite('China', 'US')).toBe('mismatch');
    expect(deliveryMatchesSite(undefined, 'US')).toBe('unknown');
    expect(deliveryMatchesSite('London SW1A 1AA', 'GB')).toBe('match');
    expect(deliveryMatchesSite('Berlin 10115', 'DE')).toBe('match');
  });

  it('配送地址不符 → 整轮停止；读不到不据此停止', () => {
    const ok = { kind: 'ok' as const, items: [item('B01')], hasNext: false };
    expect(
      decideAfterPage({ scan: { ...ok, deliveryText: 'China' }, page: 1, maxPages: 2, accumulated: [], cap: 1000, site: 'US' }),
    ).toEqual({ action: 'stop_run', reason: 'wrong_delivery' });
    expect(decideAfterPage({ scan: ok, page: 1, maxPages: 2, accumulated: [], cap: 1000, site: 'US' })).toEqual({
      action: 'report',
      items: [item('B01')],
    });
  });

  it('配送地址不符时，即使第 1 页为空也是整轮停止而不是这家店失败', () => {
    expect(
      decideAfterPage({
        scan: { kind: 'ok', items: [], hasNext: false, deliveryText: 'China' },
        page: 1,
        maxPages: 2,
        accumulated: [],
        cap: 1000,
        site: 'US',
      }),
    ).toEqual({ action: 'stop_run', reason: 'wrong_delivery' });
  });
});

describe('auto-config', () => {
  it('默认关闭', () => {
    expect(normalizeAutoConfig(undefined)).toEqual(DEFAULT_AUTO_CONFIG);
    expect(DEFAULT_AUTO_CONFIG.enabled).toBe(false);
  });

  it('非法值回落默认、数值被夹到范围内、开关只认 true', () => {
    expect(normalizeAutoConfig({ enabled: 'yes', dailyTime: '25:00', pages: 99, maxStores: 0 })).toEqual({
      enabled: false,
      dailyTime: '08:00',
      pages: 5,
      maxStores: 1,
    });
  });

  it('今天的时刻已过 → 明天；未到 → 今天', () => {
    const now = new Date(2026, 10, 24, 9, 0, 0);
    expect(new Date(nextDailyRunAt('08:00', now)).getDate()).toBe(25);
    expect(new Date(nextDailyRunAt('10:30', now)).getDate()).toBe(24);
  });
});
