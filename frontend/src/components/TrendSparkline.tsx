import { Tooltip, Typography } from 'antd';
import { useId, useState } from 'react';
import { formatScopeDateLabel } from '@/pages/Analysis/scopeDateLabel';
import { TREND_COLORS, TREND_LABEL_COLOR } from './trendColors';

/**
 * 排名标签的字号与上下两条基线的位置。
 *
 * 标签**交替**置于折线上下两侧：排名可达六位数（实测某站单期 75% 的行 ≥ 100000），
 * `433,961` 在 9px 字号下约 38px 宽，七个点等距排在同一行需要约 294px，而列宽只有
 * 190px。交替之后同侧相邻标签的间距翻倍到约 52px，38px 的标签留有余量。
 *
 * 标签固定在这两条基线上、**不追随折线的 y 值**：追随会与折线交叠，而固定基线只损失
 * 一点标签与点的视觉关联（由 x 对齐承担）。
 */
const LABEL_FONT_SIZE = 9;
const LABEL_BAND = 14;

export default function TrendSparkline({
  view = 'DAY',
  values,
  dates,
  width = 168,
  // 高度含上下两条标签带。行高已由商品卡牌撑到约 100px，这里的增高不改变行高——
  // 趋势列不是决定行高的那一列，这是交替标注方案成立的前提。
  height = 72,
}: {
  view?: 'DAY' | 'WEEK';
  values?: number[];
  dates?: string[];
  width?: number;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const gradientId = useId().replace(/:/g, '');
  if (!values?.length) return <span style={{ color: '#ccc' }}>—</span>;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;
  // 折线只用中间那一段高度，上下各让出一条标签带
  const plotTop = LABEL_BAND;
  const plotHeight = height - LABEL_BAND * 2;
  const px = (i: number) => (n === 1 ? width / 2 : (i / (n - 1)) * (width - 10) + 5);
  const py = (v: number) => ((v - min) / range) * (plotHeight - 10) + 5 + plotTop;
  const points = values.map((value, index) => ({ x: px(index), y: py(value) }));
  const linePoints = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
  const areaBase = height - LABEL_BAND;
  const areaPoints = `${points[0].x.toFixed(1)},${areaBase} ${linePoints} ${points[n - 1].x.toFixed(1)},${areaBase}`;
  // 口径是**整段走势**（首点 vs 末点），不是相邻两期之比——见 trendColors 的说明
  const color =
    values[n - 1] < values[0]
      ? TREND_COLORS.up
      : values[n - 1] > values[0]
        ? TREND_COLORS.down
        : TREND_COLORS.flat;
  const date = active === null ? undefined : dates?.[active];
  const dateLabel = date
    ? formatScopeDateLabel(date, view)
    : `第 ${active === null ? 1 : active + 1} ${view === 'WEEK' ? '周' : '天'}`;

  return (
    <Tooltip
      open={active !== null}
      title={
        active === null ? null : (
          <div style={{ minWidth: 130 }}>
            <div>{dateLabel}</div>
            <div>
              <span style={{ color: '#91caff' }}>●</span>{' '}
              <Typography.Text style={{ color: '#fff' }}>ABA排名：</Typography.Text>
              <strong>{values[active].toLocaleString()}</strong>
            </div>
          </div>
        )
      }
    >
      <svg
        width={width}
        height={height}
        style={{ verticalAlign: 'middle', cursor: 'crosshair' }}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
          setActive(Math.round(ratio * (n - 1)));
        }}
        onMouseLeave={() => setActive(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <polygon points={areaPoints} fill={`url(#${gradientId})`} stroke="none" />
        <polyline
          data-testid="trend-line"
          points={linePoints}
          fill="none"
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/*
          各点排名常驻显示，不只靠悬浮：表格的用途是横向比较多行，而逐点悬停读数
          做不了比较。偶数下标标在上带、奇数下标标在下带，使同侧间距翻倍。
        */}
        {values.map((value, index) => (
          <text
            key={index}
            x={px(index)}
            y={index % 2 === 0 ? LABEL_BAND - 5 : height - 4}
            textAnchor={index === 0 ? 'start' : index === n - 1 ? 'end' : 'middle'}
            fontSize={LABEL_FONT_SIZE}
            fill={TREND_LABEL_COLOR}
          >
            {value.toLocaleString()}
          </text>
        ))}
        {active !== null && (
          <>
            <line x1={px(active)} x2={px(active)} y1={2} y2={height - 2} stroke="#8c8c8c" />
            <circle cx={px(active)} cy={py(values[active])} r={3} fill="#1677ff" stroke="#fff" />
          </>
        )}
      </svg>
    </Tooltip>
  );
}
