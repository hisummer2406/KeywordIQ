import { Tooltip } from 'antd';

/**
 * 走势迷你图（自绘 SVG，不引图表库）。数组按日期升序（旧→新），从左到右绘制，
 * 右端为最新。排名越小越好，故 y 轴反向：最优排名在顶部。面积填充 + 首末对比着色
 * （末位排名更小=上升=绿，更大=红），右端标当前点；hover 显示排名序列（旧→新）。
 */
export default function TrendSparkline({
  values,
  width = 84,
  height = 26,
}: {
  values?: number[];
  width?: number;
  height?: number;
}) {
  if (!values || values.length === 0) {
    return <span style={{ color: '#ccc' }}>—</span>;
  }

  const tip = `近 ${values.length} 天排名（旧 → 新）：${values.join(' → ')}`;

  if (values.length === 1) {
    return (
      <Tooltip title={tip}>
        <svg width={width} height={height} style={{ verticalAlign: 'middle' }}>
          <circle cx={width / 2} cy={height / 2} r={2.5} fill="#1677ff" />
        </svg>
      </Tooltip>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;
  const px = (i: number) => (i / (n - 1)) * (width - 6) + 3;
  const py = (v: number) => ((v - min) / range) * (height - 6) + 3; // 排名 min(最优) → 顶部

  const line = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const area = `3,${height - 3} ${line} ${px(n - 1).toFixed(1)},${height - 3}`;
  const first = values[0];
  const last = values[n - 1];
  const color = last < first ? '#3f8600' : last > first ? '#cf1322' : '#8c8c8c';

  return (
    <Tooltip title={tip}>
      <svg width={width} height={height} style={{ verticalAlign: 'middle' }}>
        <polygon points={area} fill={`${color}1f`} stroke="none" />
        <polyline points={line} fill="none" stroke={color} strokeWidth={1.5} />
        {/* 起点（旧）淡标 + 终点（新）实心，强化左→右时间方向 */}
        <circle cx={px(0)} cy={py(first)} r={1.5} fill={color} opacity={0.4} />
        <circle cx={px(n - 1)} cy={py(last)} r={2.5} fill={color} />
      </svg>
    </Tooltip>
  );
}
