import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { CalcResult, OptionResult } from '@/services/pricing';
import ResultPanel from './ResultPanel';

// services/pricing 引入 @umijs/max 的 request；不 mock 会把 Umi 打包器（esbuild）拉进 jsdom
vi.mock('@umijs/max', () => ({ request: vi.fn() }));

function option(over: Partial<OptionResult> = {}): OptionResult {
  return {
    market: 'US',
    option_key: 'FBM:C1',
    label: '测试 专线',
    availability: 'available',
    reasons: [],
    solve_status: 'met',
    evaluation: {
      price: '93.75',
      revenue_cny: '93.75',
      net_revenue_cny: '93.75',
      lines: [],
      profit_cny: '40.00',
      margin: '0.4266',
      roi: null,
    },
    target_price: '93.75',
    breakeven_price: '43.75',
    max_purchase_cost: null,
    target_gap_cny: '0.00',
    adjusted: null,
    adjusted_met: null,
    adjusted_gap_cny: null,
    recommended: true,
    rules: [],
    transit_days: null,
    ...over,
  };
}

function result(markets: CalcResult['markets']): CalcResult {
  return { model_version: '1', calc_date: '2026-11-10', mode: 'target_profit', markets, excluded_fees: ['低库存费'] };
}

const market = (over: Partial<CalcResult['markets'][number]> = {}) => ({
  market: 'US',
  currency: 'USD',
  fx_rate: '7.1',
  price_includes_vat: false,
  status: 'ok' as const,
  reasons: [],
  options: [option()],
  competitors: [],
  ...over,
});

describe('核价结果展示', () => {
  it('可用状态与达标状态分开展示，推荐只在市场内', () => {
    render(<ResultPanel result={result([market()])} stale={false} priceOverrides={{}} />);
    expect(screen.getByText('可用')).toBeTruthy();
    expect(screen.getByText('达标')).toBeTruthy();
    expect(screen.getByText('推荐')).toBeTruthy();
    expect(screen.getAllByText('93.75').length).toBeGreaterThan(0);
  });

  it('待补资料逐条列出原因，不显示达标标签', () => {
    const o = option({
      option_key: 'FBM:C2',
      availability: 'needs_input',
      reasons: ['待补资料：关税税率'],
      solve_status: 'not_computed',
      evaluation: null,
      target_price: null,
      recommended: false,
    });
    render(<ResultPanel result={result([market({ options: [o] })])} stale={false} priceOverrides={{}} />);
    expect(screen.getByText('待补资料')).toBeTruthy();
    expect(screen.getByText('待补资料：关税税率')).toBeTruthy();
    expect(screen.queryByText('达标')).toBeNull();
  });

  it('区分无结果的原因：缺少费用配置不会显示成「没有渠道」', () => {
    render(
      <ResultPanel
        result={result([market({ market: 'DE', currency: 'EUR', status: 'missing_config', reasons: ['缺少费用配置：汇率'], options: [] })])}
        stale={false}
        priceOverrides={{}}
      />,
    );
    expect(screen.getByText('缺少费用配置，该市场无法核算')).toBeTruthy();
    expect(screen.getByText('缺少费用配置：汇率')).toBeTruthy();
  });

  it('条件已修改时提示过期（PRICE-11）', () => {
    render(<ResultPanel result={result([market()])} stale priceOverrides={{}} />);
    expect(screen.getByText('条件已修改，请重新核价')).toBeTruthy();
  });

  it('试算结果不提供选定按钮；已保存的最新版本才可选定', () => {
    const { rerender } = render(<ResultPanel result={result([market()])} stale={false} priceOverrides={{}} />);
    // antd 会在两个汉字的按钮文字间插空格（「选 定」），按角色匹配
    expect(screen.queryByRole('button', { name: /选\s*定/ })).toBeNull();
    rerender(
      <ResultPanel result={result([market()])} stale={false} priceOverrides={{}} canSelect onSelect={() => undefined} />,
    );
    expect(screen.getByRole('button', { name: /选\s*定/ })).toBeTruthy();
  });
});
