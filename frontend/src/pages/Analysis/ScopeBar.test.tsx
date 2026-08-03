import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ScopeBar from './ScopeBar';

describe('ScopeBar', () => {
  it('将整组作用域控件水平居中并保留自动换行', () => {
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

    const scopeBar = container.firstElementChild;
    expect(scopeBar).toHaveStyle({
      width: '100%',
      justifyContent: 'center',
      flexWrap: 'wrap',
    });
    expect(container).toHaveTextContent('🇺🇸 美国站（US）');
  });
});
