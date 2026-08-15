import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TrendSparkline from './TrendSparkline';
import { TREND_COLORS, TREND_LABEL_COLOR } from './trendColors';

/** 图上常驻的排名标签（不含悬浮提示里的那份）。 */
function labelsOf(container: HTMLElement) {
  return [...container.querySelectorAll('text')];
}

describe('TrendSparkline', () => {
  it('鼠标移动到走势点时显示对应日期与排名', async () => {
    const { container } = render(
      <TrendSparkline values={[30, 20, 10]} dates={['2026-07-16', '2026-07-17', '2026-07-18']} />,
    );
    const svg = container.querySelector('svg') as SVGSVGElement;
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, width: 120, top: 0, right: 120, bottom: 36, height: 36 }),
    });

    fireEvent.mouseMove(svg, { clientX: 60 });

    expect(await screen.findByText('2026年07月17日')).toBeInTheDocument();
    // 排名现在图上也常驻显示，"20" 因此在文档里有两处；这里要断言的是**悬浮提示**里
    // 那一处，故按 strong 定位——图上的标签是 <text>。
    expect(screen.getByText('20', { selector: 'strong' })).toBeInTheDocument();
  });

  it('多个数据点严格按原始值绘制折线，不做平滑插值', () => {
    const { container } = render(<TrendSparkline values={[30, 12, 26, 8]} />);

    const line = container.querySelector('[data-testid="trend-line"]');
    expect(line?.tagName.toLowerCase()).toBe('polyline');
    // 最大值落在绘图区顶端：折线区已让出上下两条标签带，故 y 自 LABEL_BAND 起算
    expect(line).toHaveAttribute('points', expect.stringContaining('5.0,53.0'));
    expect(container.querySelector('path[data-testid="trend-line"]')).not.toBeInTheDocument();
  });

  it('周视图的日期提示复用时间搜索的周序号与日期范围文案', async () => {
    const { container } = render(
      <TrendSparkline view="WEEK" values={[30, 20, 10]} dates={['2026-06-27', '2026-07-04', '2026-07-11']} />,
    );
    const svg = container.querySelector('svg') as SVGSVGElement;
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, width: 120, top: 0, right: 120, bottom: 36, height: 36 }),
    });

    fireEvent.mouseMove(svg, { clientX: 60 });

    expect(await screen.findByText('2026年第27周（06/28–07/04）')).toBeInTheDocument();
  });

  it('周视图 4 期数据渲染 4 个点，与行窗口同宽', () => {
    const { container } = render(
      <TrendSparkline
        view="WEEK"
        values={[40, 30, 20, 10]}
        dates={['2026-06-27', '2026-07-04', '2026-07-11', '2026-07-18']}
      />,
    );

    const points = container
      .querySelector('[data-testid="trend-line"]')!
      .getAttribute('points')!
      .trim()
      .split(/\s+/);
    expect(points).toHaveLength(4);
  });
});

describe('各点排名常驻标注', () => {
  it('七个点的排名全部直接显示在图上，无需悬浮', () => {
    // 六位数是常态：实测某站单期 75% 的行 ≥ 100000
    const { container } = render(
      <TrendSparkline values={[433961, 120345, 98000, 4200, 305, 42, 7]} />,
    );

    expect(labelsOf(container).map((t) => t.textContent)).toEqual([
      '433,961', '120,345', '98,000', '4,200', '305', '42', '7',
    ]);
  });

  it('相邻两点的标签分居折线上下两侧，避免六位数横向压盖', () => {
    const { container } = render(<TrendSparkline values={[10, 20, 30, 40]} />);
    const ys = labelsOf(container).map((t) => Number(t.getAttribute('y')));

    // 同奇偶的点共用一条基线，相邻的分属两条——这正是让同侧间距翻倍的机制
    expect(ys[0]).toBe(ys[2]);
    expect(ys[1]).toBe(ys[3]);
    expect(ys[0]).not.toBe(ys[1]);
  });

  it('标签用中性色，不随升降变色', () => {
    // 首 1 → 末 100：整段变差，折线是红的，但数字不该跟着红
    const { container } = render(<TrendSparkline values={[1, 100]} />);

    // 先钉住"确实有标签"——否则空数组会让下面的循环空转，测试假绿
    expect(labelsOf(container)).toHaveLength(2);
    for (const label of labelsOf(container)) {
      expect(label.getAttribute('fill')).toBe(TREND_LABEL_COLOR);
      expect(label.getAttribute('fill')).not.toBe(TREND_COLORS.down);
    }
  });

  it('缺期时只标已有的点，不为缺失期留空标签', () => {
    const { container } = render(<TrendSparkline values={[10, 20, 30]} />);

    expect(labelsOf(container)).toHaveLength(3);
  });

  it('周视图 4 个点同样常驻标注', () => {
    const { container } = render(<TrendSparkline view="WEEK" values={[40, 30, 20, 10]} />);

    expect(labelsOf(container).map((t) => t.textContent)).toEqual(['40', '30', '20', '10']);
  });
});

describe('折线颜色的口径', () => {
  it('取共享色值 token，与排名变化列同源', () => {
    const { container } = render(<TrendSparkline values={[100, 50]} />);

    expect(container.querySelector('[data-testid="trend-line"]')).toHaveAttribute(
      'stroke',
      TREND_COLORS.up,
    );
  });

  it('由整段走势决定，不由末期变化决定', () => {
    // 首 100 → 末 150 整段变差（红）；但末点相对倒数第二点 160→150 是改善。
    // 若哪天有人把颜色改由 ranking_change 驱动，这条会红——那正是要拦的改法。
    const { container } = render(<TrendSparkline values={[100, 120, 160, 150]} />);

    expect(container.querySelector('[data-testid="trend-line"]')).toHaveAttribute(
      'stroke',
      TREND_COLORS.down,
    );
  });

  it('首末持平时为无变化色', () => {
    const { container } = render(<TrendSparkline values={[50, 10, 50]} />);

    expect(container.querySelector('[data-testid="trend-line"]')).toHaveAttribute(
      'stroke',
      TREND_COLORS.flat,
    );
  });
});
