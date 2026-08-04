import { Tooltip, Typography } from 'antd';
import { useId, useState } from 'react';
import { formatScopeDateLabel } from '@/pages/Analysis/scopeDateLabel';

export default function TrendSparkline({
  view = 'DAY',
  values,
  dates,
  width = 168,
  height = 52,
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
  const px = (i: number) => (n === 1 ? width / 2 : (i / (n - 1)) * (width - 10) + 5);
  const py = (v: number) => ((v - min) / range) * (height - 10) + 5;
  const points = values.map((value, index) => ({ x: px(index), y: py(value) }));
  const linePoints = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
  const areaPoints = `${points[0].x.toFixed(1)},${height - 4} ${linePoints} ${points[n - 1].x.toFixed(1)},${height - 4}`;
  const color =
    values[n - 1] < values[0] ? '#52c41a' : values[n - 1] > values[0] ? '#ff4d4f' : '#1677ff';
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
