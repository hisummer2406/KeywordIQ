import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { KeywordTrendPoint } from '@/services/keywordTrend';
import TrendTimelineChart from './TrendTimelineChart';

/**
 * 真实时间轴：与列表页 `TrendSparkline`（等距排点）刻意不同——这里按真实日期定位，
 * 且非相邻的 RANKED 点之间（中间隔着非在榜状态）不连线，见组件顶部注释。
 */

function point(date: string, status: KeywordTrendPoint['status'], ranking: number | null = null): KeywordTrendPoint {
  return { date, status, ranking };
}

describe('TrendTimelineChart', () => {
  it('没有任何期次时显示无数据，不渲染图表', () => {
    render(<TrendTimelineChart points={[]} view="DAY" />);
    expect(screen.getByText('无数据')).toBeInTheDocument();
  });

  it('全部为非在榜状态时显示提示文案，但仍渲染每个期次的标记', () => {
    const points = [point('2026-07-01', 'NOT_IMPORTED'), point('2026-07-02', 'OUT_OF_RETENTION')];
    const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

    expect(screen.getByText('所选范围内没有在榜排名')).toBeInTheDocument();
    expect(container.querySelector('[data-testid="trend-point-NOT_IMPORTED-2026-07-01"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="trend-point-OUT_OF_RETENTION-2026-07-02"]')).toBeTruthy();
  });

  it('相邻的两个在榜点之间画线', () => {
    const points = [point('2026-07-01', 'RANKED', 100), point('2026-07-02', 'RANKED', 90)];
    const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

    expect(container.querySelectorAll('[data-testid="trend-line-segment"]')).toHaveLength(1);
  });

  it('两个在榜点之间隔着缺口时不连线', () => {
    const points = [
      point('2026-07-01', 'RANKED', 100),
      point('2026-07-02', 'NOT_IMPORTED'),
      point('2026-07-03', 'RANKED', 90),
    ];
    const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

    expect(container.querySelectorAll('[data-testid="trend-line-segment"]')).toHaveLength(0);
    expect(container.querySelector('[data-testid="trend-point-RANKED-2026-07-01"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="trend-point-RANKED-2026-07-03"]')).toBeTruthy();
  });

  it('单个在榜点正常渲染，不因为只有一个点而报错或不显示', () => {
    const points = [point('2026-07-10', 'RANKED', 50)];
    const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

    expect(container.querySelector('[data-testid="trend-point-RANKED-2026-07-10"]')).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="trend-line-segment"]')).toHaveLength(0);
  });

  it('掉榜点不参与连线，即便与在榜点相邻', () => {
    const points = [
      point('2026-07-01', 'RANKED', 100),
      point('2026-07-02', 'DROPPED'),
      point('2026-07-03', 'RANKED', 90),
    ];
    const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

    expect(container.querySelectorAll('[data-testid="trend-line-segment"]')).toHaveLength(0);
    expect(container.querySelector('[data-testid="trend-point-DROPPED-2026-07-02"]')).toBeTruthy();
  });

  /**
   * 悬停留下的 `activeIndex` 是上一次 `points` 数组下的下标；父组件（抽屉）切换范围/
   * 视图后传入更短的新数组，这个下标可能越界。`points[activeIndex]` 越界时是
   * `undefined` 不是 `null`——渲染层若只判断 `active !== null` 就会在越界这一刻仍然
   * 试图解引用 `active.date`，直接抛错崩掉整个抽屉，而不是这里能单独重渲染验证的组件
   * 边界（PR #87 复审第四轮 P2）。
   */
  it('悬停后收到更短的新数组，越界下标不崩溃、也不再显示提示', () => {
    const longPoints = [
      point('2026-07-01', 'RANKED', 100),
      point('2026-07-02', 'RANKED', 90),
      point('2026-07-03', 'RANKED', 80),
    ];
    const { container, rerender } = render(<TrendTimelineChart points={longPoints} view="DAY" />);

    const svg = container.querySelector('svg')!;
    // jsdom 的 getBoundingClientRect 恒为全零矩形，relX 直接等于 clientX；给一个远大于
    // 绘图宽度的值，最近点必然落在数组最后一个下标（索引 2）。
    fireEvent.mouseMove(svg, { clientX: 10000, clientY: 100 });
    expect(container.querySelector('[data-testid="trend-point-RANKED-2026-07-03"]')).toHaveAttribute('r', '5');

    const shortPoints = [point('2026-07-10', 'RANKED', 50)];
    expect(() => rerender(<TrendTimelineChart points={shortPoints} view="DAY" />)).not.toThrow();
    expect(screen.queryByText('2026年07月03日')).not.toBeInTheDocument();
  });
});
