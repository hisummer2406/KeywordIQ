import { Tooltip } from 'antd';
import { useId, useMemo, useState } from 'react';
import { formatScopeDateLabel } from '@/pages/Analysis/scopeDateLabel';
import type { KeywordTrendPoint, TrendPointStatus } from '@/services/keywordTrend';
import type { View } from '@/services/analysis';
import { TREND_COLORS } from '../trendColors';

/**
 * 排名趋势详情的时间轴图表。
 *
 * 与列表页的 `TrendSparkline` **刻意不同**：那里是等距排点（固定 7/4 个点，回答"这批行
 * 一路怎么走"），这里的 x 坐标按**真实日期线性映射**——详情的范围可以有缺口（未导入/
 * 已超保留/已导入未收录），等距排布会把这些缺口的时间跨度抹平，让用户读到假的连续性。
 *
 * 折线只连接**数组中相邻且都是 RANKED** 的两个点——非相邻（中间隔着非在榜状态的点）
 * 或任一端不是 RANKED 都不连线，这是"不跨缺口连线"的直接实现，不是事后过滤。
 */

const STATUS_META: Record<TrendPointStatus, { label: string; color: string }> = {
  RANKED: { label: '在榜', color: TREND_COLORS.flat },
  DROPPED: { label: '已掉榜', color: '#8c8c8c' },
  NO_DATA: { label: '该期报表已导入但未收录', color: '#bfbfbf' },
  NOT_IMPORTED: { label: '该期报表未导入', color: '#d9d9d9' },
  OUT_OF_RETENTION: { label: '已超出保留范围', color: '#f0f0f0' },
};

const HEIGHT = 220;
const PAD_X = 28;
const PAD_TOP = 16;
const PAD_BOTTOM = 36;
const BASELINE_Y = HEIGHT - PAD_BOTTOM;

function toTime(dateStr: string): number {
  return new Date(`${dateStr}T00:00:00Z`).getTime();
}

export default function TrendTimelineChart({
  points,
  view,
  width = 760,
}: {
  points: KeywordTrendPoint[];
  view: View;
  width?: number;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const gradientId = useId().replace(/:/g, '');

  const { xOf, yOf, ranked, plotTop, plotHeight } = useMemo(() => {
    const times = points.map((p) => toTime(p.date));
    const minT = Math.min(...times);
    const maxT = Math.max(...times);
    const spanT = maxT - minT || 1;
    const x = (i: number) => PAD_X + ((times[i] - minT) / spanT) * (width - PAD_X * 2);

    const rankedPts = points.filter((p) => p.status === 'RANKED' && p.ranking !== null);
    const rankings = rankedPts.map((p) => p.ranking as number);
    const minR = rankings.length ? Math.min(...rankings) : 0;
    const maxR = rankings.length ? Math.max(...rankings) : 1;
    const spanR = maxR - minR || 1;
    const top = PAD_TOP;
    const plotH = BASELINE_Y - PAD_TOP;
    // 排名越小越靠上：y 与 ranking 正相关（ranking 大 → y 大 → 越靠下）
    const y = (ranking: number) => top + ((ranking - minR) / spanR) * (plotH - 12) + 6;

    return { xOf: x, yOf: y, ranked: rankedPts.length > 0, plotTop: top, plotHeight: plotH };
  }, [points, width]);

  if (points.length === 0) {
    return <div style={{ color: '#999', padding: 24, textAlign: 'center' }}>无数据</div>;
  }

  // 折线段：数组中相邻且都是 RANKED 的两点之间画线；单点 RANKED 只画点，不画线。
  const segments: { x1: number; y1: number; x2: number; y2: number }[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    if (a.status === 'RANKED' && b.status === 'RANKED' && a.ranking !== null && b.ranking !== null) {
      segments.push({ x1: xOf(i), y1: yOf(a.ranking), x2: xOf(i + 1), y2: yOf(b.ranking) });
    }
  }

  const active = activeIndex === null ? null : points[activeIndex];

  return (
    <Tooltip
      open={active !== null}
      title={
        active === null ? null : (
          <div style={{ minWidth: 160 }}>
            <div>{formatScopeDateLabel(active.date, view)}</div>
            <div>
              {STATUS_META[active.status].label}
              {active.status === 'RANKED' && active.ranking !== null
                ? `：ABA 排名 ${active.ranking.toLocaleString()}`
                : ''}
            </div>
          </div>
        )
      }
    >
      <svg
        width={width}
        height={HEIGHT}
        style={{ cursor: 'crosshair' }}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const relX = event.clientX - rect.left;
          // 按最近的真实 x 坐标定位，不是按下标等分——两者在有缺口时不等价。
          let nearest = 0;
          let best = Infinity;
          points.forEach((_, i) => {
            const d = Math.abs(xOf(i) - relX);
            if (d < best) {
              best = d;
              nearest = i;
            }
          });
          setActiveIndex(nearest);
        }}
        onMouseLeave={() => setActiveIndex(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={TREND_COLORS.flat} stopOpacity="0.18" />
            <stop offset="100%" stopColor={TREND_COLORS.flat} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        <line
          x1={PAD_X}
          x2={width - PAD_X}
          y1={BASELINE_Y}
          y2={BASELINE_Y}
          stroke="#f0f0f0"
        />

        {!ranked && (
          <text x={width / 2} y={plotTop + plotHeight / 2} textAnchor="middle" fill="#bfbfbf" fontSize={13}>
            所选范围内没有在榜排名
          </text>
        )}

        {segments.map((seg, i) => (
          <line
            key={i}
            data-testid="trend-line-segment"
            x1={seg.x1}
            y1={seg.y1}
            x2={seg.x2}
            y2={seg.y2}
            stroke={TREND_COLORS.flat}
            strokeWidth={2}
            strokeLinecap="round"
          />
        ))}

        {points.map((p, i) => {
          const meta = STATUS_META[p.status];
          const cx = xOf(i);
          if (p.status === 'RANKED' && p.ranking !== null) {
            return (
              <circle
                key={p.date}
                data-testid={`trend-point-RANKED-${p.date}`}
                cx={cx}
                cy={yOf(p.ranking)}
                r={activeIndex === i ? 5 : 3.5}
                fill={meta.color}
                stroke="#fff"
                strokeWidth={1}
              />
            );
          }
          // 非在榜状态：统一画在基线上，用形状/透明度区分五态里的另外四态。
          return (
            <g key={p.date} data-testid={`trend-point-${p.status}-${p.date}`}>
              <circle
                cx={cx}
                cy={BASELINE_Y}
                r={activeIndex === i ? 4.5 : 3}
                fill={p.status === 'DROPPED' ? meta.color : 'none'}
                stroke={meta.color}
                strokeWidth={1.5}
              />
            </g>
          );
        })}
      </svg>
    </Tooltip>
  );
}

export { STATUS_META };
