import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listStores, addStore, updateStore, removeStore, listStoreProducts } = vi.hoisted(() => ({
  listStores: vi.fn(),
  addStore: vi.fn(),
  updateStore: vi.fn(),
  removeStore: vi.fn(),
  listStoreProducts: vi.fn(),
}));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/competitor', () => ({
  listStores,
  addStore,
  updateStore,
  removeStore,
  listStoreProducts,
}));

import StoreTab from './StoreTab';
import { formatMoney } from './storeColumns';

function store(overrides: Record<string, unknown> = {}) {
  return {
    domain: 'example.com',
    name: '示例店',
    currency: 'GBP',
    country: 'CN',
    group_id: null,
    group_name: null,
    note: null,
    watched_at: '2026-09-01T00:00:00Z',
    status: 'OK',
    catalog_complete: true,
    rank_ok: true,
    consecutive_failures: 0,
    last_fetch_at: '2026-09-01T02:00:00Z',
    first_fetch_at: '2026-08-30T02:00:00Z',
    product_count: 120,
    new_in_7d: 0,
    ...overrides,
  };
}

function renderTab() {
  return render(
    <StoreTab groups={[]} activeGroupId={undefined} onGroupsChanged={async () => undefined} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listStores.mockResolvedValue({ success: true, data: [store()], total: 1 });
});

describe('四种状态互不合并', () => {
  it('刚加入的站点显示「待首次拉取」，不是「抓取失败」——它不是坏了', async () => {
    listStores.mockResolvedValue({
      success: true,
      data: [store({ status: 'NEVER_FETCHED', last_fetch_at: null, rank_ok: false })],
      total: 1,
    });
    renderTab();

    expect(await screen.findByText('待首次拉取')).toBeTruthy();
    expect(screen.queryByText(/抓取失败/)).toBeNull();
    // 还没拉过时不报 0，否则会被读成「这家店没有商品/没上新」
    expect(screen.getAllByText('待拉取').length).toBe(2);
  });

  it('商户关掉商品接口时单独成一种状态，不混进抓取失败', async () => {
    listStores.mockResolvedValue({
      success: true,
      data: [store({ status: 'NO_CATALOG_API' })],
      total: 1,
    });
    renderTab();

    expect(await screen.findByText('未开放商品接口')).toBeTruthy();
    expect(screen.queryByText(/抓取失败/)).toBeNull();
  });

  it('抓取失败带上连续次数', async () => {
    listStores.mockResolvedValue({
      success: true,
      data: [store({ status: 'FAILED', consecutive_failures: 3 })],
      total: 1,
    });
    renderTab();

    expect(await screen.findByText('抓取失败（连续 3 次）')).toBeTruthy();
  });

  it('拉取不完整单独成一种状态', async () => {
    listStores.mockResolvedValue({
      success: true,
      data: [store({ status: 'INCOMPLETE', catalog_complete: false })],
      total: 1,
    });
    renderTab();

    expect(await screen.findByText('数据不完整')).toBeTruthy();
  });
});

describe('畅销榜解析异常独立呈现', () => {
  it('解析异常时单独打标，不显示成「销量榜为空」', async () => {
    listStores.mockResolvedValue({
      success: true,
      data: [store({ rank_ok: false })],
      total: 1,
    });
    renderTab();

    expect(await screen.findByText('畅销榜解析异常')).toBeTruthy();
    // 目录仍然是好的——两个标记相互独立
    expect(screen.getByText('监控中')).toBeTruthy();
  });

  it('一切正常时不打这个标', async () => {
    renderTab();
    await screen.findByText('监控中');

    expect(screen.queryByText('畅销榜解析异常')).toBeNull();
  });
});

describe('近 7 日上架显示真实数字', () => {
  it('拉过之后显示真实的 0，不是占位符', async () => {
    renderTab();
    await screen.findByText('监控中');

    // 独立站有真实数据源，0 就是「这周没上新」；Amazon 卖家那张表相反（必须占位符）
    await waitFor(() => expect(screen.getByText('0')).toBeTruthy());
    expect(screen.queryByText('待拉取')).toBeNull();
  });
});

describe('空状态分两种成因', () => {
  it('一个都没关注时指路「添加独立站」', async () => {
    listStores.mockResolvedValue({ success: true, data: [], total: 0 });
    renderTab();

    expect(await screen.findByText(/点击「添加独立站」粘贴店铺域名/)).toBeTruthy();
  });

  it('选中分组为空时说的是这个分组里没有', async () => {
    listStores.mockResolvedValue({ success: true, data: [], total: 0 });
    render(
      <StoreTab groups={[]} activeGroupId={7} onGroupsChanged={async () => undefined} />,
    );

    expect(await screen.findByText('这个分组里还没有独立站')).toBeTruthy();
  });
});

describe('币种', () => {
  it('已知币种显示符号，未知时只显示数字——不臆造符号', () => {
    expect(formatMoney('18.98', 'USD')).toBe('$18.98');
    expect(formatMoney('12.99', 'GBP')).toBe('£12.99');
    // 没收录符号的币种显示币种码，不猜
    expect(formatMoney('100', 'SEK')).toBe('100 SEK');
    // 币种未知时绝不加符号——加错了比不加更糟
    expect(formatMoney('18.98', null)).toBe('18.98');
    expect(formatMoney(null, 'USD')).toBe('—');
  });
});
