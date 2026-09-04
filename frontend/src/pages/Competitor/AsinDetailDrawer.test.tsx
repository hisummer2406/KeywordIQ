import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const { getAsinDetail } = vi.hoisted(() => ({
  getAsinDetail: vi.fn(),
}));

vi.mock('@/services/competitor', () => ({ getAsinDetail }));

import AsinDetailDrawer from './AsinDetailDrawer';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AsinDetailDrawer', () => {
  it('展示首次发现时刻，且最近采集时刻带上"卖家级近似值"的说明', async () => {
    getAsinDetail.mockResolvedValue({
      success: true,
      first_seen_at: '2026-08-02T00:00:00Z',
      seller_last_reported_at: '2026-08-20T00:00:00Z',
    });

    render(
      <AsinDetailDrawer
        open
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0AAAAAAAA"
        title="Test ASIN"
        onClose={() => undefined}
      />,
    );

    expect(getAsinDetail).toHaveBeenCalledWith('AS2G99DA1EUF', 'US', 'B0AAAAAAAA');
    expect(await screen.findByText('首次发现于：')).toBeInTheDocument();
    expect(await screen.findByText('该卖家最近一次采集于：')).toBeInTheDocument();
    // 免责说明必须常驻显示，不是靠 hover 才看得到
    expect(
      await screen.findByText(/不代表这个商品本身的采集时刻/),
    ).toBeInTheDocument();
  });

  it('卖家从未被再次采集时不显示为空', async () => {
    getAsinDetail.mockResolvedValue({
      success: true,
      first_seen_at: '2026-08-02T00:00:00Z',
      seller_last_reported_at: null,
    });

    render(
      <AsinDetailDrawer
        open
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0AAAAAAAA"
        title="Test ASIN"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText('暂无记录')).toBeInTheDocument();
  });

  it('加载失败时展示错误提示', async () => {
    getAsinDetail.mockRejectedValue(new Error('网络异常'));

    render(
      <AsinDetailDrawer
        open
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0AAAAAAAA"
        title="Test ASIN"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText('网络异常')).toBeInTheDocument();
  });

  it('切换 ASIN 时旧响应不得覆盖新抽屉', async () => {
    // 不比较具体的本地化时间字符串——渲染依赖测试环境时区，CI 上不一定是 UTC+8。
    // 改用"有没有最近采集时刻"这个布尔性质的差异来判定新响应有没有被旧响应覆盖：
    // 新（先到）的响应没有该字段 → 显示"暂无记录"；旧（慢到）的响应带着该字段。
    let resolveFirst: (value: any) => void;
    let resolveSecond: (value: any) => void;
    const first = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise((resolve) => {
      resolveSecond = resolve;
    });
    getAsinDetail.mockImplementationOnce(() => first).mockImplementationOnce(() => second);

    const { rerender } = render(
      <AsinDetailDrawer
        open
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0AAAAAAAA"
        title="ASIN 一"
        onClose={() => undefined}
      />,
    );
    rerender(
      <AsinDetailDrawer
        open
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0BBBBBBBB"
        title="ASIN 二"
        onClose={() => undefined}
      />,
    );

    // ASIN 二（后发出、先到达）：没有最近采集时刻
    resolveSecond!({
      success: true,
      first_seen_at: '2026-08-10T00:00:00Z',
      seller_last_reported_at: null,
    });
    expect(await screen.findByText('暂无记录')).toBeInTheDocument();

    // ASIN 一（先发出、姗姗来迟）：带着最近采集时刻——若覆盖了 ASIN 二的展示，
    // "暂无记录"会消失
    resolveFirst!({
      success: true,
      first_seen_at: '2026-01-01T00:00:00Z',
      seller_last_reported_at: '2026-01-05T00:00:00Z',
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });

    expect(screen.getByText('暂无记录')).toBeInTheDocument();
  });

  it('未打开时不发起请求', () => {
    render(
      <AsinDetailDrawer
        open={false}
        sellerId="AS2G99DA1EUF"
        site="US"
        asin="B0AAAAAAAA"
        title="Test ASIN"
        onClose={() => undefined}
      />,
    );
    expect(getAsinDetail).not.toHaveBeenCalled();
  });
});
