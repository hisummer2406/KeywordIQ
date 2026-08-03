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
        dirty={false}
        onScopeChange={vi.fn()}
        onQChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(container.querySelector('.scope-bar__query')).toBeInTheDocument();
    expect(container).toHaveTextContent('🇺🇸 美国站（US）');
    expect(container).toHaveTextContent('2026年07月18日');
    expect(
      screen.getByPlaceholderText('输入 ASIN 或关键词，如 B07Z82895W 或 iphone charger'),
    ).toBeInTheDocument();
  });
});
