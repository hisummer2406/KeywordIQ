import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listNewArrivals } = vi.hoisted(() => ({ listNewArrivals: vi.fn() }));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/competitor', () => ({ listNewArrivals }));

import NewArrivalsTab from './NewArrivalsTab';

function row(overrides: Record<string, unknown> = {}) {
  return {
    source: 'DTC',
    source_key: 'example.com',
    source_name: '示例店',
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
  it('空流说的是「没有上新」，不是「还没查」', async () => {
    listNewArrivals.mockResolvedValue({ success: true, data: [], total: 0 });
    render(<NewArrivalsTab />);

    expect(await screen.findByText('这段时间里，你关注的竞对没有上新')).toBeTruthy();
  });
});
