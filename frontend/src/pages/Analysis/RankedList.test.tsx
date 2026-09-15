import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * antd 的 `Typography.Paragraph` 是否真的渲染出可交互的省略号提示，取决于它在真实
 * 布局下测出的截断状态（`ResizeObserver`/文本测量），jsdom 里量不出真实尺寸，这条
 * 分支在测试环境里天然不可靠。改为直接拦截 `Typography.Paragraph` 收到的 `ellipsis`
 * 配置本身——这是 `RankedList` 真正控制的那一层，不依赖 jsdom 测不出的布局细节。
 */
const { paragraphCalls } = vi.hoisted(() => ({ paragraphCalls: [] as any[] }));
vi.mock('antd', async () => {
  const actual = await vi.importActual<typeof import('antd')>('antd');
  return {
    ...actual,
    Typography: {
      ...actual.Typography,
      Paragraph: (props: any) => {
        paragraphCalls.push(props);
        return <actual.Typography.Paragraph {...props} />;
      },
    },
  };
});

import RankedList from './RankedList';

beforeEach(() => {
  paragraphCalls.length = 0;
});

describe('RankedList 悬浮历史包装（issue #96）', () => {
  it('不传 wrapValue 时，省略号提示照常显示完整值（类目列的既有行为不变）', () => {
    render(<RankedList values={['Electronics', null, null]} rows={1} />);
    expect(paragraphCalls[0].ellipsis.tooltip).toBe('Electronics');
  });

  it('传了 wrapValue 且值非空时，禁用省略号自带的提示——全名交给外层悬浮面板展示', () => {
    render(
      <RankedList
        values={['Acme Corp', null, null]}
        wrapValue={(_i, value, node) => <span data-testid="wrapped">{node}</span>}
      />,
    );
    expect(paragraphCalls[0].ellipsis.tooltip).toBe(false);
    expect(screen.getByTestId('wrapped')).toBeTruthy();
  });

  it('传了 wrapValue 但这一行是空值时，不受影响（没有全名要展示，也没有面板可包）', () => {
    render(
      <RankedList
        values={[null, 'Second', null]}
        wrapValue={(_i, value, node) => <span data-testid="wrapped">{node}</span>}
      />,
    );
    // 第一行是空值：tooltip 配置沿用「空值不给 tooltip」的既有逻辑（v ?? undefined）
    expect(paragraphCalls[0].ellipsis.tooltip).toBeUndefined();
    // 第二行非空：正常被包装且抑制自带提示
    expect(paragraphCalls[1].ellipsis.tooltip).toBe(false);
  });
});
