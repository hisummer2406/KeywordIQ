import { render, screen } from '@testing-library/react';
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
});
