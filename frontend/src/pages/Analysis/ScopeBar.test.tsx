import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ScopeBar from './ScopeBar';

describe('ScopeBar', () => {
  it('将作用域控件组合成连续查询条', () => {
    const { container } = render(
      <ScopeBar
        sites={[{ code: 'US', name_zh: '美国', domain: 'amazon.com' }]}
        availableDates={['2026-07-18']}
        scope={{ site: 'US', view: 'DAY', date: '2026-07-18' }}
        q=""
        dirty
        onScopeChange={vi.fn()}
        onQChange={vi.fn()}
        onQModeChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(container.querySelector('.scope-bar__query')).toBeInTheDocument();
    expect(container).toHaveTextContent('🇺🇸 美国站（US）');
    expect(container).toHaveTextContent('2026年07月18日');
    expect(
      screen.getByPlaceholderText('输入 ASIN 或关键词，如 B07Z82895W 或 iphone charger'),
    ).toBeInTheDocument();
    expect(container.querySelector('.scope-bar__dirty')).toHaveTextContent(
      '存在未生效条件，请点击对应的查询或筛选按钮',
    );
  });

  it('关键词输入显示匹配方式选择，ASIN 输入不显示（ASIN 是等值匹配，没有模式）', () => {
    const props = {
      sites: [{ code: 'US', name_zh: '美国', domain: 'amazon.com' }],
      availableDates: ['2026-07-18'],
      scope: { site: 'US', view: 'DAY' as const, date: '2026-07-18' },
      dirty: false,
      onScopeChange: vi.fn(),
      onQChange: vi.fn(),
      onQModeChange: vi.fn(),
      onSubmit: vi.fn(),
    };
    const { container, rerender } = render(<ScopeBar {...props} q="faucet sink" />);
    expect(container.querySelector('.scope-bar__mode')).toBeInTheDocument();
    expect(container).toHaveTextContent('全部词');

    rerender(<ScopeBar {...props} q="faucet sink" qMode="PHRASE" />);
    expect(container).toHaveTextContent('连续词组');

    rerender(<ScopeBar {...props} q="B07Z82895W" />);
    expect(container.querySelector('.scope-bar__mode')).not.toBeInTheDocument();
  });
});
