import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ScopeBar from './ScopeBar';

const baseProps = () => ({
  sites: [{ code: 'US', name_zh: '美国', domain: 'amazon.com' }],
  availableDates: ['2026-07-18'],
  scope: { site: 'US', view: 'DAY' as const, date: '2026-07-18' },
  dirty: false,
  onScopeChange: vi.fn(),
  onQChange: vi.fn(),
  onQModeChange: vi.fn(),
  onSubmit: vi.fn(),
});

const asins = (n: number) => Array.from({ length: n }, (_, i) => `B0X${String(i).padStart(7, '0')}`);

describe('ScopeBar', () => {
  it('将作用域控件组合成连续查询条，搜索方式是检索框前的独立控件', () => {
    const { container } = render(<ScopeBar {...baseProps()} q="" dirty />);

    const query = container.querySelector('.scope-bar__query')!;
    expect(query).toBeInTheDocument();
    expect(container).toHaveTextContent('美国站（US）');
    expect(container).toHaveTextContent('2026年07月18日');
    // 顺序：站点、视图、期次、搜索方式、检索框、立即查询——都在同一个 Compact 里
    const order = [...query.children].map((el) =>
      ['site', 'view', 'date', 'mode', 'search', 'submit'].find((k) =>
        el.classList.contains(`scope-bar__${k}`),
      ),
    );
    expect(order).toEqual(['site', 'view', 'date', 'mode', 'search', 'submit']);
    // 不再嵌在输入框里
    expect(container.querySelector('.scope-bar__search .scope-bar__mode')).not.toBeInTheDocument();
    expect(container).toHaveTextContent('关键词 · 全部词');
    expect(screen.getByPlaceholderText('输入关键词，如 iphone charger')).toBeInTheDocument();
    expect(container.querySelector('.scope-bar__dirty')).toHaveTextContent(
      '存在未生效条件，请点击对应的查询或筛选按钮',
    );
  });

  it('三个搜索方式都可见，所选方式回显在触发器上', async () => {
    const props = baseProps();
    const { container, rerender } = render(<ScopeBar {...props} q="B07Z82895W" />);
    // 形如 ASIN 的输入不会改变方式——方式只由用户选择决定
    expect(container.querySelector('.scope-bar__mode')).toHaveTextContent('关键词 · 全部词');

    fireEvent.mouseDown(container.querySelector('.scope-bar__mode .ant-select-selector')!);
    for (const label of ['关键词 · 全部词', '关键词 · 词组', 'ASIN · 批量']) {
      expect(
        await screen.findByText(label, { selector: '.ant-select-item-option-content' }),
      ).toBeInTheDocument();
    }
    fireEvent.click(screen.getByText('ASIN · 批量', { selector: '.ant-select-item-option-content' }));
    expect(props.onQModeChange.mock.calls[0][0]).toBe('ASIN');

    rerender(<ScopeBar {...props} q="" qMode="PHRASE" />);
    expect(container.querySelector('.scope-bar__mode')).toHaveTextContent('关键词 · 词组');
  });

  it('ASIN 方式显示去重后的个数', () => {
    render(<ScopeBar {...baseProps()} q="b0aaaaaaa1 B0AAAAAAA1, 3161484100" qMode="ASIN" />);
    expect(screen.getByText('2 个')).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText('输入或粘贴 ASIN，空格/逗号/换行分隔，最多 100 个'),
    ).toBeInTheDocument();
  });

  it('超过 100 个时提示原因，按钮与回车都不提交', () => {
    const props = baseProps();
    render(<ScopeBar {...props} q={asins(101).join(' ')} qMode="ASIN" />);

    expect(screen.getByText('ASIN 最多 100 个，当前 101 个')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /立即查询/ })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText('搜索内容'), { key: 'Enter', keyCode: 13 });
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it('恰好 100 个可以提交', () => {
    const props = baseProps();
    render(<ScopeBar {...props} q={asins(100).join(' ')} qMode="ASIN" />);
    expect(screen.getByRole('button', { name: /立即查询/ })).toBeEnabled();
    fireEvent.keyDown(screen.getByLabelText('搜索内容'), { key: 'Enter', keyCode: 13 });
    expect(props.onSubmit).toHaveBeenCalledTimes(1);
  });

  it('ASIN 方式粘贴多行时保留编号间的分隔', () => {
    const props = baseProps();
    render(<ScopeBar {...props} q="" qMode="ASIN" />);
    fireEvent.paste(screen.getByLabelText('搜索内容'), {
      clipboardData: { getData: () => 'B0AAAAAAA1\r\nB0BBBBBBB2\tB0CCCCCCC3\r\n' },
    });
    expect(props.onQChange).toHaveBeenCalledWith('B0AAAAAAA1 B0BBBBBBB2 B0CCCCCCC3');
  });

  it('关键词方式粘贴不拦截', () => {
    const props = baseProps();
    render(<ScopeBar {...props} q="" />);
    fireEvent.paste(screen.getByLabelText('搜索内容'), {
      clipboardData: { getData: () => 'coffee\nmaker' },
    });
    expect(props.onQChange).not.toHaveBeenCalled();
  });
});
