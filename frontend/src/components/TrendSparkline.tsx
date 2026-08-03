import { Tooltip, Typography } from 'antd';
import { useId, useState } from 'react';

export default function TrendSparkline({
  values,
  dates,
  width = 168,
  height = 52,
}: {
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
  const curve = points.slice(0, -1).reduce(
    (path, point, index) => {
      const next = points[index + 1];
      const before = points[index - 1] ?? point;
      const after = points[index + 2] ?? next;
      const cp1x = point.x + (next.x - before.x) / 6;
      const cp1y = point.y + (next.y - before.y) / 6;
      const cp2x = next.x - (after.x - point.x) / 6;
      const cp2y = next.y - (after.y - point.y) / 6;
      return `${path} C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${next.x.toFixed(1)},${next.y.toFixed(1)}`;
    },
    `M ${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`,
  );
  const area = `${curve} L ${points[n - 1].x.toFixed(1)},${height - 4} L ${points[0].x.toFixed(1)},${height - 4} Z`;
  const color =
    values[n - 1] < values[0] ? '#52c41a' : values[n - 1] > values[0] ? '#ff4d4f' : '#1677ff';
  const date = active === null ? undefined : dates?.[active];

  return (
    <Tooltip
      open={active !== null}
      title={
        active === null ? null : (
          <div style={{ minWidth: 130 }}>
            <div>{date ?? `第 ${active + 1} 天`}</div>
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
        <path d={area} fill={`url(#${gradientId})`} stroke="none" />
        <path
          data-testid="trend-line"
          d={curve}
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
