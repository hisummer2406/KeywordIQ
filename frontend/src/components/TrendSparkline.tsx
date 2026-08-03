import { Tooltip, Typography } from 'antd';
import { useState } from 'react';

export default function TrendSparkline({
  values,
  dates,
  width = 120,
  height = 36,
}: {
  values?: number[];
  dates?: string[];
  width?: number;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  if (!values?.length) return <span style={{ color: '#ccc' }}>—</span>;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;
  const px = (i: number) => (n === 1 ? width / 2 : (i / (n - 1)) * (width - 10) + 5);
  const py = (v: number) => ((v - min) / range) * (height - 10) + 5;
  const line = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const area = `5,${height - 5} ${line} ${px(n - 1).toFixed(1)},${height - 5}`;
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
        <polygon points={area} fill={`${color}14`} stroke="none" />
        <polyline points={line} fill="none" stroke={color} strokeWidth={1.5} />
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
