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
// 左右留白不再对称：左边要放排名刻度文字（六位数排名需要更宽的空间），右边只需容纳
// 最后一个点的半径，不需要跟左边同宽。
const PAD_LEFT = 52;
const PAD_RIGHT = 16;
const PAD_TOP = 16;
const PAD_BOTTOM = 32;
const BASELINE_Y = HEIGHT - PAD_BOTTOM;
// y 轴刻度条数（不含基线本身）；x 轴标签上限——点数超过这个数就按等距下标抽样，
// 而不是每个点都标日期，否则详情范围拉到「全部可用历史」时几十个日期会挤成一团。
const Y_TICK_COUNT = 4;
const X_LABEL_MAX = 6;

function toTime(dateStr: string): number {
  return new Date(`${dateStr}T00:00:00Z`).getTime();
}

/** 轴上的日期标签用简短的「MM/DD」，不复用悬浮提示的完整格式（含年份/周区间）——
 * 那是给单点详情看的，六个标签一起摆出来会挤爆横向空间。两者信息来源相同，只是
 * 呈现的详略程度不同，不是又造了一套日期口径。 */
function axisDateLabel(dateStr: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(dateStr);
  return match ? `${match[1]}/${match[2]}` : dateStr;
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

  const {
    xOf, yOf, ranked, plotTop, plotHeight, minR, maxR,
  } = useMemo(() => {
    const times = points.map((p) => toTime(p.date));
    const minT = Math.min(...times);
    const maxT = Math.max(...times);
    const spanT = maxT - minT || 1;
    const x = (i: number) => PAD_LEFT + ((times[i] - minT) / spanT) * (width - PAD_LEFT - PAD_RIGHT);

    const rankedPts = points.filter((p) => p.status === 'RANKED' && p.ranking !== null);
    const rankings = rankedPts.map((p) => p.ranking as number);
    const rMin = rankings.length ? Math.min(...rankings) : 0;
    const rMax = rankings.length ? Math.max(...rankings) : 1;
    const spanR = rMax - rMin || 1;
    const top = PAD_TOP;
    const plotH = BASELINE_Y - PAD_TOP;
    // 排名越小越靠上：y 与 ranking 正相关（ranking 大 → y 大 → 越靠下）
    const y = (ranking: number) => top + ((ranking - rMin) / spanR) * (plotH - 12) + 6;

    return {
      xOf: x, yOf: y, ranked: rankedPts.length > 0, plotTop: top, plotHeight: plotH, minR: rMin, maxR: rMax,
    };
  }, [points, width]);

  // y 轴刻度：按「最好排名（minR，画在上方）→ 最差排名（maxR，画在下方）」等分。
  // 只有一个在榜值时（minR === maxR）只标这一个刻度，不强行摊出 5 条重合的线。
  const yTicks = useMemo(() => {
    if (!ranked) return [];
    if (minR === maxR) return [minR];
    return Array.from(
      { length: Y_TICK_COUNT + 1 },
      (_, i) => Math.round(minR + ((maxR - minR) * i) / Y_TICK_COUNT),
    );
  }, [ranked, minR, maxR]);

  // x 轴标签：点数超过上限时按等距下标抽样，但恒保留首尾——「全部可用历史」范围可能
  // 有几十个期次，每个都标日期会挤成一团看不清任何一个。
  const xLabelIndices = useMemo(() => {
    if (points.length <= X_LABEL_MAX) return points.map((_, i) => i);
    const step = (points.length - 1) / (X_LABEL_MAX - 1);
    const picked = new Set<number>();
    for (let i = 0; i < X_LABEL_MAX; i += 1) picked.add(Math.round(i * step));
    return [...picked].sort((a, b) => a - b);
  }, [points.length]);

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

  // `activeIndex` 可能是切换范围/视图前留下的旧下标：新响应落地后 `points` 换成一个
  // 更短的数组，旧下标越界。`points[activeIndex]` 此时是 `undefined` 不是 `null`——
  // 用 `?? null` 兜底，否则下方 `active !== null` 仍判真，渲染时解引用 `active.date`
  // 直接崩掉整个抽屉（PR #87 复审第四轮 P2）。
  const active = activeIndex === null ? null : (points[activeIndex] ?? null);

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

        {/* y 轴：排名刻度线（浅色横向网格线）+ 左侧数值标签。数值越小排名越好，
            画在越上方——与下方各点的映射（yOf）同一套方向。 */}
        {yTicks.map((tick) => (
          <g key={`y-${tick}`}>
            <line
              x1={PAD_LEFT}
              x2={width - PAD_RIGHT}
              y1={yOf(tick)}
              y2={yOf(tick)}
              stroke="#f5f5f5"
            />
            <text
              data-testid="trend-y-tick"
              x={PAD_LEFT - 8}
              y={yOf(tick) + 4}
              textAnchor="end"
              fill="#8c8c8c"
              fontSize={11}
            >
              {tick.toLocaleString()}
            </text>
          </g>
        ))}

        {/* x 轴：轴线本身 + 底部日期刻度。轴线兼作基线（在榜/非在榜的点都以它为参照）。 */}
        <line
          x1={PAD_LEFT}
          x2={width - PAD_RIGHT}
          y1={BASELINE_Y}
          y2={BASELINE_Y}
          stroke="#d9d9d9"
        />
        <line
          x1={PAD_LEFT}
          x2={PAD_LEFT}
          y1={PAD_TOP}
          y2={BASELINE_Y}
          stroke="#d9d9d9"
        />
        {xLabelIndices.map((i) => (
          <g key={`x-${points[i].date}`}>
            <line
              x1={xOf(i)}
              x2={xOf(i)}
              y1={BASELINE_Y}
              y2={BASELINE_Y + 4}
              stroke="#d9d9d9"
            />
            <text
              data-testid="trend-x-tick"
              x={xOf(i)}
              y={BASELINE_Y + 16}
              textAnchor="middle"
              fill="#8c8c8c"
              fontSize={11}
            >
              {axisDateLabel(points[i].date)}
            </text>
          </g>
        ))}

        {!ranked && (
          <text
            x={PAD_LEFT + (width - PAD_LEFT - PAD_RIGHT) / 2}
            y={plotTop + plotHeight / 2}
            textAnchor="middle"
            fill="#bfbfbf"
            fontSize={13}
          >
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
