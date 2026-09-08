import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tiktokAdLibraryUrl } from './tiktokAdLibraryUrl';

const FIXED_NOW = new Date('2026-09-05T00:00:00.000Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(FIXED_NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('tiktokAdLibraryUrl', () => {
  it('GB/DE 在覆盖范围内，站点码直接当 region 参数用', () => {
    const url = tiktokAdLibraryUrl('power bank', 'GB')!;
    expect(url).toContain('https://library.tiktok.com/ads?');
    expect(url).toContain('region=GB');
    expect(url).toContain('adv_name=power+bank');

    expect(tiktokAdLibraryUrl('power bank', 'DE')).toContain('region=DE');
  });

  it('时间窗口是当前时刻往前 31 天的毫秒时间戳', () => {
    const url = new URL(tiktokAdLibraryUrl('power bank', 'GB')!);
    expect(url.searchParams.get('end_time')).toBe(String(FIXED_NOW));
    expect(url.searchParams.get('start_time')).toBe(String(FIXED_NOW - 31 * 24 * 60 * 60 * 1000));
  });

  it('US 不在覆盖范围内，返回 null（实测该库没有 United States 这个国家选项）', () => {
    expect(tiktokAdLibraryUrl('power bank', 'US')).toBeNull();
  });

  it('站点码缺失或不识别时返回 null', () => {
    expect(tiktokAdLibraryUrl('power bank', undefined)).toBeNull();
    expect(tiktokAdLibraryUrl('power bank', '')).toBeNull();
    expect(tiktokAdLibraryUrl('power bank', 'XX')).toBeNull();
  });

  it('关键词为空时返回 null', () => {
    expect(tiktokAdLibraryUrl('', 'GB')).toBeNull();
    expect(tiktokAdLibraryUrl('   ', 'GB')).toBeNull();
  });
});
