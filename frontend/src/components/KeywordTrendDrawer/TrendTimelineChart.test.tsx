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
   *
   * 不断言 Tooltip 内容是否挂在 DOM 上——antd Tooltip 关闭时走退场动画，`forceRender`/
   * 动画期间节点可能短暂仍在 DOM 里（这条在本地过、CI 上翻红，就是这个环境时序差异），
   * 断言这个没有意义。真正要守的是"不崩"与"越界后没有任何点被继续标记为悬停中"。
   */
  it('悬停后收到更短的新数组，越界下标不崩溃、也不再把任何点标记为悬停中', () => {
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
    // 越界的下标 2 在新数组（长度 1）里不对应任何点，唯一剩下的点不应被画成悬停态（r=5）
    expect(container.querySelector('[data-testid="trend-point-RANKED-2026-07-10"]')).toHaveAttribute('r', '3.5');
  });

  describe('可用的横纵坐标系（issue #94）', () => {
    it('有在榜点时渲染 y 轴刻度，标签为该范围内的最好与最差排名', () => {
      const points = [
        point('2026-07-01', 'RANKED', 100),
        point('2026-07-02', 'RANKED', 20),
      ];
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      const yTicks = container.querySelectorAll('[data-testid="trend-y-tick"]');
      expect(yTicks.length).toBeGreaterThan(0);
      const texts = [...yTicks].map((el) => el.textContent);
      expect(texts).toContain('20');
      expect(texts).toContain('100');
    });

    it('只有一个在榜值时只渲染一条 y 轴刻度，不强行摊出多条重合的线', () => {
      const points = [point('2026-07-01', 'RANKED', 50), point('2026-07-02', 'RANKED', 50)];
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      const yTicks = container.querySelectorAll('[data-testid="trend-y-tick"]');
      expect(yTicks).toHaveLength(1);
      expect(yTicks[0].textContent).toBe('50');
    });

    it('排名范围很窄时（等分后取整会撞出重复值）y 轴刻度去重，不渲染重叠的刻度线（Codex 审核 PR #95）', () => {
      // minR=100 maxR=101，五等分取整为 [100,100,101,101,101]——去重前会撞出重复的
      // `key={`y-${tick}`}` 与重叠的网格线/文字标签，去重后应只剩 100 与 101 两条。
      const points = [point('2026-07-01', 'RANKED', 100), point('2026-07-02', 'RANKED', 101)];
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      const yTicks = container.querySelectorAll('[data-testid="trend-y-tick"]');
      const texts = [...yTicks].map((el) => el.textContent);
      expect(texts).toEqual(['100', '101']);
    });

    it('排名达到百万级时左边距按刻度文字位数动态撑开，不裁掉前几位数字（Codex 审核 PR #95 第二轮）', () => {
      // 固定左边距 52px 时，"1,000,000"（9 字符）右对齐画在 x=44，比默认六位数场景
      // （"100,000" 7 字符）宽得多，会越过 x=0 被 SVG 根元素默认的 overflow:hidden
      // 裁掉前几位——两个点同一排名只产生一条刻度（minR === maxR 分支），便于精确断言。
      const points = [
        point('2026-07-01', 'RANKED', 1000000),
        point('2026-07-08', 'RANKED', 1000000),
      ];
      const { container } = render(<TrendTimelineChart points={points} view="WEEK" />);

      const yTick = container.querySelector('[data-testid="trend-y-tick"]')!;
      expect(yTick.textContent).toBe('1,000,000');
      // PAD_LEFT = max(52, 9 字符 × 7 + 12) = 75，刻度文字画在 PAD_LEFT - 8 = 67，
      // 比默认场景的 44 更靠右，此时才不会有任何数字落在 x<0 的裁剪区域内。
      expect(Number(yTick.getAttribute('x'))).toBe(67);
      const axisLine = container.querySelectorAll('svg > line')[1]; // 第二条 line 是竖直轴线
      expect(Number(axisLine.getAttribute('x1'))).toBe(75);
    });

    it('全部为非在榜状态时不渲染 y 轴刻度（没有排名数值可标）', () => {
      const points = [point('2026-07-01', 'NOT_IMPORTED'), point('2026-07-02', 'OUT_OF_RETENTION')];
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      expect(container.querySelectorAll('[data-testid="trend-y-tick"]')).toHaveLength(0);
      // x 轴日期刻度与状态无关，两个期次都应标出
      expect(container.querySelectorAll('[data-testid="trend-x-tick"]')).toHaveLength(2);
    });

    it('x 轴日期标签按简短的 MM/DD 呈现，且恒包含首尾两个期次', () => {
      const points = [
        point('2026-07-01', 'RANKED', 100),
        point('2026-07-15', 'RANKED', 90),
        point('2026-07-30', 'RANKED', 80),
      ];
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      const texts = [...container.querySelectorAll('[data-testid="trend-x-tick"]')].map((el) => el.textContent);
      expect(texts).toContain('07/01');
      expect(texts).toContain('07/30');
    });

    it('点数超过上限时按等距抽样标注，不是每个点都标日期', () => {
      const points = Array.from({ length: 30 }, (_, i) => {
        const day = String(i + 1).padStart(2, '0');
        return point(`2026-07-${day}`, 'RANKED', 100 - i);
      });
      const { container } = render(<TrendTimelineChart points={points} view="DAY" />);

      const xTicks = container.querySelectorAll('[data-testid="trend-x-tick"]');
      expect(xTicks.length).toBeLessThan(points.length);
      const texts = [...xTicks].map((el) => el.textContent);
      expect(texts).toContain('07/01');
      expect(texts).toContain('07/30');
    });
  });
});
