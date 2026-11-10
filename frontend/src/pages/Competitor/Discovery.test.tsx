import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  listNewArrivals,
  listGroups,
  getProductHistory,
  getAsinDetail,
  getSites,
  setSearchParams,
  searchState,
} = vi.hoisted(() => ({
  listNewArrivals: vi.fn(),
  listGroups: vi.fn(),
  getProductHistory: vi.fn(),
  getAsinDetail: vi.fn(),
  getSites: vi.fn(),
  setSearchParams: vi.fn(),
  // 筛选写在 URL 里；测试里用一个可改的 URLSearchParams 模拟当前地址栏
  searchState: { params: new URLSearchParams() },
}));

vi.mock('@umijs/max', () => ({
  useAccess: () => ({}),
  useSearchParams: () => [searchState.params, setSearchParams],
}));
vi.mock('@/services/competitor', () => ({
  listNewArrivals,
  listGroups,
  getProductHistory,
  getAsinDetail,
}));
vi.mock('@/services/upload', () => ({ getSites }));

import DiscoveryPage, { filtersFromParams } from './Discovery';

function row(overrides: Record<string, unknown> = {}) {
  return {
    source: 'DTC',
    source_key: 'example.com',
    source_name: '示例店',
    site: null,
    product_key: '1',
    title: '一个新品',
    image_url: null,
    at: '2026-09-01T02:00:00Z',
    time_kind: 'PUBLISHED',
    discovered_at: '2026-09-02T02:00:00Z',
    published_at: '2026-09-01T02:00:00Z',
    product_url: 'https://example.com/products/one',
    ...overrides,
  };
}

function amazonRow(overrides: Record<string, unknown> = {}) {
  return row({
    source: 'AMAZON',
    source_key: 'A1TZ946F8HZTNY',
    source_name: null,
    site: 'US',
    product_key: 'B0H8N7QNW4',
    time_kind: 'DISCOVERED',
    at: '2026-09-02T02:00:00Z',
    discovered_at: '2026-09-02T02:00:00Z',
    published_at: null,
    product_url: 'https://www.amazon.com/dp/B0H8N7QNW4',
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  searchState.params = new URLSearchParams();
  listNewArrivals.mockResolvedValue({ success: true, data: [row()], total: 1 });
  listGroups.mockResolvedValue([]);
  getSites.mockResolvedValue([{ code: 'US', name_zh: '美国', domain: 'amazon.com' }]);
  getProductHistory.mockResolvedValue({
    success: true,
    domain: 'example.com',
    product_id: 1,
    currency: 'USD',
    prices: [],
    ranks: [],
  });
  getAsinDetail.mockResolvedValue({
    success: true,
    first_seen_at: '2026-08-02T00:00:00Z',
    seller_last_reported_at: null,
  });
});

describe('两类来源的时间语义必须分开说', () => {
  it('Shopify 行同时给出「上架于」（数据源事实）与「首次发现于」', async () => {
    render(<DiscoveryPage />);

    expect(await screen.findByText('上架于')).toBeTruthy();
    expect(screen.getByText('首次发现于')).toBeTruthy();
  });

  it('Amazon 行只有「首次发现于」，不能冒充「上架于」', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [amazonRow()], total: 1 });
    render(<DiscoveryPage />);

    expect(await screen.findByText('首次发现于')).toBeTruthy();
    expect(screen.queryByText('上架于')).toBeNull();
  });

  it('来源标记是 Amazon 与 Shopify，界面不出现「独立站」', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [row(), amazonRow()], total: 2 });
    render(<DiscoveryPage />);

    expect(await screen.findByText('Shopify')).toBeTruthy();
    expect(screen.getByText('Amazon')).toBeTruthy();
    expect(screen.queryByText(/独立站/)).toBeNull();
  });
});

describe('看品', () => {
  it('能直接打开原商品，链接在新窗口打开', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [amazonRow()], total: 1 });
    render(<DiscoveryPage />);

    const link = (await screen.findByText('打开原商品')).closest('a')!;
    expect(link.getAttribute('href')).toBe('https://www.amazon.com/dp/B0H8N7QNW4');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(screen.getByText('ASIN B0H8N7QNW4')).toBeTruthy();
  });

  it('缺图、缺店名、缺链接时如实降级：显示「无图」、回落 seller_id、不出链接', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [amazonRow({ image_url: null, source_name: null, product_url: null })],
      total: 1,
    });
    render(<DiscoveryPage />);

    expect(await screen.findByText('无图')).toBeTruthy();
    expect(screen.getByText('A1TZ946F8HZTNY')).toBeTruthy();
    expect(screen.queryByText('打开原商品')).toBeNull();
  });

  it('有店名时显示店名而不是 seller_id', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [amazonRow({ source_name: 'Acme Store' })],
      total: 1,
    });
    render(<DiscoveryPage />);

    expect(await screen.findByText('Acme Store')).toBeTruthy();
    expect(screen.queryByText('A1TZ946F8HZTNY')).toBeNull();
  });
});

describe('筛选都传到后端，并写在 URL 里', () => {
  it('URL 里的筛选原样进请求体', async () => {
    searchState.params = new URLSearchParams('days=30&source=AMAZON&site=DE&group=3&seller=A1X');
    render(<DiscoveryPage />);

    await waitFor(() => expect(listNewArrivals).toHaveBeenCalled());
    expect(listNewArrivals.mock.calls[0][0]).toMatchObject({
      days: 30,
      source: 'AMAZON',
      site: 'DE',
      groupId: 3,
      sellerId: 'A1X',
    });
  });

  it('非法参数落回缺省，不把看不懂的值发给后端', () => {
    expect(filtersFromParams(new URLSearchParams('days=5&source=EBAY&group=abc'))).toEqual({
      days: 7,
      source: undefined,
      site: undefined,
      groupId: undefined,
      sellerId: undefined,
      domain: undefined,
    });
  });

  it('从店铺管理带过来的店铺显示为可关闭的标签', async () => {
    searchState.params = new URLSearchParams('domain=example.com');
    render(<DiscoveryPage />);

    expect(await screen.findByText('Shopify 店铺 example.com')).toBeTruthy();
  });

  it('切到「Shopify 店铺」时清掉 Amazon 站点——Shopify 没有站点，留着只会恒为空', async () => {
    searchState.params = new URLSearchParams('site=DE');
    const user = userEvent.setup();
    render(<DiscoveryPage />);

    await user.click(await screen.findByText('Shopify 店铺'));
    expect(setSearchParams).toHaveBeenCalledWith({ source: 'DTC' });
  });
});

describe('同一 seller_id 跨站点卖同一 ASIN', () => {
  it('两行都渲染，且各自标出自己的站点——共享层的真实粒度是 (seller_id, site, asin)', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [amazonRow(), amazonRow({ site: 'GB', at: '2026-09-01T03:00:00Z' })],
      total: 2,
    });
    render(<DiscoveryPage />);

    // 两行都渲染出来了（没有因为 rowKey 撞车而互相顶掉）
    expect((await screen.findAllByText('A1TZ946F8HZTNY')).length).toBe(2);
    expect(screen.getByText('US')).toBeTruthy();
    expect(screen.getByText('GB')).toBeTruthy();
  });
});

describe('首刷语义与空状态', () => {
  it('明示只列出纳入监控之后新出现的商品', async () => {
    render(<DiscoveryPage />);

    expect(await screen.findByText(/这里只列出「纳入监控之后」新出现的商品/)).toBeTruthy();
  });

  it('空列表的措辞不承诺"没有上新"', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [], total: 0 });
    render(<DiscoveryPage />);

    expect(await screen.findByText('当前筛选条件下暂无新出现的商品')).toBeTruthy();
  });
});

describe('点开一行看详情', () => {
  it('点击 Shopify 行触发详情请求，domain 取 source_key、productId 取 product_key', async () => {
    const user = userEvent.setup();
    render(<DiscoveryPage />);

    await user.click(await screen.findByText('一个新品'));

    expect(getProductHistory).toHaveBeenCalledWith('example.com', 1);
  });

  it('点击 Amazon 行触发详情请求，sellerId/site/asin 分别取 source_key/site/product_key', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [amazonRow()], total: 1 });
    const user = userEvent.setup();
    render(<DiscoveryPage />);

    await user.click(await screen.findByText('一个新品'));

    expect(getAsinDetail).toHaveBeenCalledWith('A1TZ946F8HZTNY', 'US', 'B0H8N7QNW4');
    // Shopify 的详情组件不该跟着开——同一时刻只应有一个抽屉打开
    expect(getProductHistory).not.toHaveBeenCalled();
  });
});
