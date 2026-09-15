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

// 四档非在榜状态改用互不相同的色相，不再是灰阶深浅——同一色系深浅差异在小圆点/图例上
// 很难一眼分辨，用户反馈"都是灰色"正是这个问题。避开 TREND_COLORS 的 up/绿、down/红、
// flat/蓝三色（那三色在别处已经承载"名次升降"与"在榜"的含义，混进来会让人把"已掉榜"
// 误读成"名次下降"），四档各取一个独立色相：
const STATUS_META: Record<TrendPointStatus, { label: string; color: string }> = {
  RANKED: { label: '在榜', color: TREND_COLORS.flat },
  DROPPED: { label: '已掉榜', color: '#d46b08' }, // 橙：真实存在过、掉出去了，四档里最该显眼
  NO_DATA: { label: '该期报表已导入但未收录', color: '#531dab' }, // 紫：报表在，只是没这个词
  NOT_IMPORTED: { label: '该期报表未导入', color: '#874d00' }, // 深金：数据还没进来
  OUT_OF_RETENTION: { label: '已超出保留范围', color: '#08979c' }, // 青：正常边界，不是问题
};

const HEIGHT = 220;
// 左右留白不再对称：左边要放排名刻度文字，右边只需容纳最后一个点的半径，不需要跟
// 左边同宽。PAD_LEFT 不再是固定值——见下方按刻度文字实际位数动态撑开的计算。
const PAD_LEFT_MIN = 52;
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

  // 先只算排名的统计量（最好/最差值），不依赖任何留白常量——下面要用它先定出
  // y 轴刻度，再用刻度文字的实际位数反过来撑开左边距（PAD_LEFT），两者不能反过来算。
  const { ranked, minR, maxR } = useMemo(() => {
    const rankedPts = points.filter((p) => p.status === 'RANKED' && p.ranking !== null);
    const rankings = rankedPts.map((p) => p.ranking as number);
    return {
      ranked: rankedPts.length > 0,
      minR: rankings.length ? Math.min(...rankings) : 0,
      maxR: rankings.length ? Math.max(...rankings) : 1,
    };
  }, [points]);

  // y 轴刻度：按「最好排名（minR，画在上方）→ 最差排名（maxR，画在下方）」等分。
  // 只有一个在榜值时（minR === maxR）只标这一个刻度，不强行摊出 5 条重合的线。
  // 排名范围很窄时（如 100~101）等分后取整会撞出重复值——[100,100,101,101,101]，
  // 去重前会画出重叠的网格线与文字、且 `key={`y-${tick}`}` 撞键（Codex 审核 PR #95
  // 第一轮 P2）。用 `Set` 去重，保留升序（Array.from 本就按 i 递增生成，Set 保留
  // 插入顺序）。
  const yTicks = useMemo(() => {
    if (!ranked) return [];
    if (minR === maxR) return [minR];
    const raw = Array.from(
      { length: Y_TICK_COUNT + 1 },
      (_, i) => Math.round(minR + ((maxR - minR) * i) / Y_TICK_COUNT),
    );
    return [...new Set(raw)];
  }, [ranked, minR, maxR]);

  // 左边距按最长的刻度文字动态撑开，不再假定六位数封顶——周报的 ABA 排名可以到
  // 百万级尾部，"1,000,000" 这类 9 字符标签在固定 52px（含千分位逗号的六位数留白）
  // 下会越过 SVG 左边界，被根元素默认的 overflow:hidden 裁掉前几位数字（Codex 审核
  // PR #95 第二轮 P2）。7px/字符是 11px sans-serif 下数字与逗号字符的保守宽度上界，
  // PAD_LEFT_MIN(52) 继续作为六位数以内场景的下限，不因为这次改动收窄留白。
  const maxTickChars = yTicks.length
    ? Math.max(...yTicks.map((t) => t.toLocaleString().length))
    : 0;
  const PAD_LEFT = Math.max(PAD_LEFT_MIN, maxTickChars * 7 + 12);

  const {
    xOf, yOf, plotTop, plotHeight,
  } = useMemo(() => {
    const times = points.map((p) => toTime(p.date));
    const minT = Math.min(...times);
    const maxT = Math.max(...times);
    const spanT = maxT - minT || 1;
    const x = (i: number) => PAD_LEFT + ((times[i] - minT) / spanT) * (width - PAD_LEFT - PAD_RIGHT);

    const spanR = maxR - minR || 1;
    const top = PAD_TOP;
    const plotH = BASELINE_Y - PAD_TOP;
    // 排名越小越靠上：y 与 ranking 正相关（ranking 大 → y 大 → 越靠下）
    const y = (ranking: number) => top + ((ranking - minR) / spanR) * (plotH - 12) + 6;

    return {
      xOf: x, yOf: y, plotTop: top, plotHeight: plotH,
    };
  }, [points, width, PAD_LEFT, minR, maxR]);

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

  // 悬浮提示的锚点坐标要跟着这个点实际画在哪——在榜点在折线上（yOf(ranking)），
  // 非在榜点在基线上（BASELINE_Y），与下方 <circle> 的 cy 取值必须是同一套逻辑，
  // 否则提示框会跟点对不上。此前 Tooltip 包住整个 <svg>，只能贴在整块图表的固定
  // 位置（近似顶部居中），既不随左右移动、也不随排名高低变化——用户反馈"应该跟着
  // 线走"（issue #94）。
  //
  // **不用 antd 的 `<Tooltip>`。** 试过用一个绝对定位的 1x1 锚点配合 `open`
  // 受控的 Tooltip，结果鼠标连续滑动、`active` 从一个点切到另一个点时提示框位置
  // 不动——rc-trigger 只在 `open` 从 false 变 true 那一刻测量锚点位置，锚点已经
  // 挂载着（`open` 全程为 true）时改它的 `left`/`top` 不会触发重新对齐（用户反馈
  // "按天的悬浮有 bug，鼠标滑动，悬浮位置不动"）。改为自己画一个绝对定位的提示框，
  // 位置完全由 React state 驱动，每次渲染都是全新计算的坐标，不依赖任何缓存的
  // 对齐结果。
  const anchorX = active !== null && activeIndex !== null ? xOf(activeIndex) : 0;
  const anchorY = active !== null && active.status === 'RANKED' && active.ranking !== null
    ? yOf(active.ranking)
    : BASELINE_Y;

  return (
    <div style={{ position: 'relative', width, height: HEIGHT }}>
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

      {active !== null && (
        // 自己画的提示框，位置每次渲染都重新算，不经过任何会缓存对齐结果的库组件
        // （见上方大注释）。`translate(-50%, -100%)` 让框水平居中在锚点上方、垂直
        // 贴在锚点上边缘，再上移 8px 留出与点的间距；`pointerEvents: 'none'` 避免
        // 提示框本身挡住 <svg> 的 onMouseMove。
        <div
          data-testid="trend-tooltip"
          style={{
            position: 'absolute',
            left: anchorX,
            top: anchorY,
            transform: 'translate(-50%, -100%) translateY(-8px)',
            background: 'rgba(0, 0, 0, 0.85)',
            color: '#fff',
            padding: '6px 10px',
            borderRadius: 4,
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
            zIndex: 10,
          }}
        >
          <div>{formatScopeDateLabel(active.date, view)}</div>
          <div>
            {STATUS_META[active.status].label}
            {active.status === 'RANKED' && active.ranking !== null
              ? `：ABA 排名 ${active.ranking.toLocaleString()}`
              : ''}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 图例常驻展示的三种状态——「在榜」「已掉榜」「该期报表未导入」是用户日常会遇到的，
 * 「该期报表已导入但未收录」与「已超出保留范围」是少见情况（用户反馈"没见过，好奇怪"）。
 *
 * **这只是图例的呈现范围，不是判定口径的合并**：五态分类本身不变，`STATUS_META`
 * 仍有五个键，图上的点、悬浮提示（`formatScopeDateLabel` 那段）该显示什么状态照旧显示
 * 真实状态——少见的两态一旦真的出现在数据里，点和悬浮提示上依然是它们各自的颜色与文案，
 * 只是不占图例的常驻位置。把这两态在判定上合并进「未导入」会违反 keyword-trend-detail
 * spec 的 MUST NOT（已超出保留范围的数据已被永久清理，不该被误读成"等一等就有"）。
 */
const COMMON_STATUSES: TrendPointStatus[] = ['RANKED', 'DROPPED', 'NOT_IMPORTED'];

export { STATUS_META, COMMON_STATUSES };
