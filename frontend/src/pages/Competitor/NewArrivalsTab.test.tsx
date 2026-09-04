import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { siteCodeToFlag } from '@/pages/Analysis/siteLabel';

const { listNewArrivals, getProductHistory } = vi.hoisted(() => ({
  listNewArrivals: vi.fn(),
  getProductHistory: vi.fn(),
}));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/competitor', () => ({ listNewArrivals, getProductHistory }));

import NewArrivalsTab from './NewArrivalsTab';

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
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listNewArrivals.mockResolvedValue({ success: true, data: [row()], total: 1 });
  getProductHistory.mockResolvedValue({
    success: true,
    domain: 'example.com',
    product_id: 1,
    currency: 'USD',
    prices: [],
    ranks: [],
  });
});

describe('两类来源的时间语义必须分开说', () => {
  it('独立站的时间标为「上架于」——那是数据源给的事实', async () => {
    render(<NewArrivalsTab />);

    expect(await screen.findByText('上架于')).toBeTruthy();
    expect(screen.queryByText('发现于')).toBeNull();
  });

  it('Amazon 的时间标为「发现于」，不能冒充「上架于」', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [
        row({
          source: 'AMAZON',
          source_key: 'A1TZ946F8HZTNY',
          source_name: null,
          product_key: 'B0H8N7QNW4',
          time_kind: 'DISCOVERED',
        }),
      ],
      total: 1,
    });
    render(<NewArrivalsTab />);

    // 标成「上架于」等于拿「我们发现得晚」冒充「他上得晚」
    expect(await screen.findByText('发现于')).toBeTruthy();
    expect(screen.queryByText('上架于')).toBeNull();
  });

  it('两类来源各自打来源标记', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [row(), row({ source: 'AMAZON', product_key: 'B0X', time_kind: 'DISCOVERED' })],
      total: 2,
    });
    render(<NewArrivalsTab />);

    expect(await screen.findByText('独立站')).toBeTruthy();
    expect(screen.getByText('亚马逊')).toBeTruthy();
  });
});

describe('同一 seller_id 跨站点卖同一 ASIN', () => {
  it('两行都渲染，且各自标出自己的站点——共享层的真实粒度是 (seller_id, site, asin)', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [
        row({
          source: 'AMAZON',
          source_key: 'A1TZ946F8HZTNY',
          source_name: null,
          site: 'US',
          product_key: 'B0H8N7QNW4',
          time_kind: 'DISCOVERED',
        }),
        row({
          source: 'AMAZON',
          source_key: 'A1TZ946F8HZTNY',
          source_name: null,
          site: 'GB',
          product_key: 'B0H8N7QNW4',
          time_kind: 'DISCOVERED',
          at: '2026-09-01T03:00:00Z',
        }),
      ],
      total: 2,
    });
    render(<NewArrivalsTab />);

    // 两行都渲染出来了（没有因为 rowKey 撞车而互相顶掉）
    expect((await screen.findAllByText('A1TZ946F8HZTNY')).length).toBe(2);
    expect(screen.getByText(siteCodeToFlag('US'))).toBeTruthy();
    expect(screen.getByText(siteCodeToFlag('GB'))).toBeTruthy();
  });
});

describe('首刷语义必须在界面上说清', () => {
  it('明示只统计纳入监控之后新出现的商品', async () => {
    render(<NewArrivalsTab />);

    // 不说的话，用户会问「我明明刚加了一家有 250 个商品的店，为什么一条都没有」
    expect(
      await screen.findByText(/这里只统计「纳入监控之后」新出现的商品/),
    ).toBeTruthy();
  });
});

describe('空状态', () => {
  it('空流的措辞不承诺"没有上新"——Amazon 侧的空流只能证明已采集范围内没有新发现', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [], total: 0 });
    render(<NewArrivalsTab />);

    expect(await screen.findByText('当前时间范围内暂无上架/新发现记录')).toBeTruthy();
  });
});

describe('点开一行看详情', () => {
  it('点击独立站行触发详情请求，domain 取 source_key、productId 取 product_key', async () => {
    const user = userEvent.setup();
    render(<NewArrivalsTab />);

    await user.click(await screen.findByText('一个新品'));

    expect(getProductHistory).toHaveBeenCalledWith('example.com', 1);
  });

  it('点击 Amazon 行不触发任何请求——Amazon 侧的详情仍是已知缺口（issue #65）', async () => {
    listNewArrivals.mockResolvedValue({
      success: true,
      data: [
        row({
          source: 'AMAZON',
          source_key: 'A1TZ946F8HZTNY',
          source_name: null,
          site: 'US',
          product_key: 'B0H8N7QNW4',
          time_kind: 'DISCOVERED',
        }),
      ],
      total: 1,
    });
    const user = userEvent.setup();
    render(<NewArrivalsTab />);

    await user.click(await screen.findByText('一个新品'));

    expect(getProductHistory).not.toHaveBeenCalled();
  });
});
