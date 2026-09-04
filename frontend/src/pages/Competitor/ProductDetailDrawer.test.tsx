import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const { getProductHistory } = vi.hoisted(() => ({
  getProductHistory: vi.fn(),
}));

vi.mock('@/services/competitor', () => ({ getProductHistory }));

import ProductDetailDrawer from './ProductDetailDrawer';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProductDetailDrawer', () => {
  it('展示多次调价记录，按响应顺序（服务端已倒序）列出', async () => {
    getProductHistory.mockResolvedValue({
      success: true,
      domain: 'blessrich.com',
      product_id: 1,
      currency: 'GBP',
      prices: [
        {
          observed_at: '2026-09-02T00:00:00Z',
          variant_id: 11,
          sku: 'SKU-A',
          price: '8.99',
          compare_at_price: '17.99',
          available: true,
        },
        {
          observed_at: '2026-08-20T00:00:00Z',
          variant_id: 11,
          sku: 'SKU-A',
          price: '12.99',
          compare_at_price: '25.99',
          available: true,
        },
      ],
      ranks: [{ captured_on: '2026-09-02', rank: 3 }],
    });

    render(
      <ProductDetailDrawer
        open
        domain="blessrich.com"
        productId={1}
        title="Test Product"
        onClose={() => undefined}
      />,
    );

    await waitFor(() => expect(getProductHistory).toHaveBeenCalledWith('blessrich.com', 1));
    // 两条价格记录都要出现，币种符号来自响应的 currency
    expect(await screen.findByText('£8.99')).toBeInTheDocument();
    expect(await screen.findByText('£12.99')).toBeInTheDocument();
    // 两条记录同一个 SKU（同一变体两次调价），必然有两处
    expect((await screen.findAllByText('SKU-A')).length).toBe(2);
    // 名次历史同时展示
    expect(await screen.findByText('第 3 名')).toBeInTheDocument();
  });

  it('自监控以来价格从未变化：只有一条记录，不显示为空', async () => {
    getProductHistory.mockResolvedValue({
      success: true,
      domain: 'blessrich.com',
      product_id: 2,
      currency: 'GBP',
      prices: [
        {
          observed_at: '2026-08-30T00:00:00Z',
          variant_id: 21,
          sku: null,
          price: '9.99',
          compare_at_price: null,
          available: true,
        },
      ],
      ranks: [],
    });

    render(
      <ProductDetailDrawer
        open
        domain="blessrich.com"
        productId={2}
        title="Test Product 2"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText('£9.99')).toBeInTheDocument();
    // sku 为 null 时退回 variant_id，不显示空白
    expect(await screen.findByText('#21')).toBeInTheDocument();
    // 没有划线价显示占位符，不显示 0
    expect(screen.getByText('—')).toBeInTheDocument();
    // 名次历史为空时给出明确文案
    expect(await screen.findByText('暂无名次记录')).toBeInTheDocument();
  });

  it('加载失败时展示错误提示，不留白屏', async () => {
    getProductHistory.mockRejectedValue(new Error('网络异常'));

    render(
      <ProductDetailDrawer
        open
        domain="blessrich.com"
        productId={3}
        title="Test Product 3"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText('网络异常')).toBeInTheDocument();
  });

  it('未打开时不发起请求', () => {
    render(
      <ProductDetailDrawer
        open={false}
        domain="blessrich.com"
        productId={1}
        title="Test Product"
        onClose={() => undefined}
      />,
    );
    expect(getProductHistory).not.toHaveBeenCalled();
  });
});
