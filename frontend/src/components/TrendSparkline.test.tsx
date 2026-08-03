import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import TrendSparkline from './TrendSparkline';

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

    expect(await screen.findByText('2026-07-17')).toBeInTheDocument();
    expect(screen.getByText('20')).toBeInTheDocument();
  });

  it('多个数据点严格按原始值绘制折线，不做平滑插值', () => {
    const { container } = render(<TrendSparkline values={[30, 12, 26, 8]} />);

    const line = container.querySelector('[data-testid="trend-line"]');
    expect(line?.tagName.toLowerCase()).toBe('polyline');
    expect(line).toHaveAttribute('points', expect.stringContaining('5.0,47.0'));
    expect(container.querySelector('path[data-testid="trend-line"]')).not.toBeInTheDocument();
  });
});
