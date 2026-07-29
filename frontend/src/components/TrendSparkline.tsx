/**
 * 极简走势迷你图（自绘 SVG，不引图表库）。传入的是排名序列——排名越小越好，
 * 故 y 轴反向：最优排名在顶部。颜色按首末对比：末位排名更小(上升)=绿，更大=红。
 */
export default function TrendSparkline({
  values,
  width = 72,
  height = 22,
}: {
  values?: number[];
  width?: number;
  height?: number;
}) {
  if (!values || values.length === 0) {
    return <span style={{ color: '#ccc' }}>—</span>;
  }
  if (values.length === 1) {
    return (
      <svg width={width} height={height} style={{ verticalAlign: 'middle' }}>
        <circle cx={width / 2} cy={height / 2} r={2.5} fill="#1677ff" />
      </svg>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const n = values.length;
  const px = (i: number) => (i / (n - 1)) * (width - 4) + 2;
  const py = (v: number) => ((v - min) / range) * (height - 4) + 2; // 排名 min(最优) → 顶部

  const points = values.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const last = values[n - 1];
  const first = values[0];
  const color = last < first ? '#3f8600' : last > first ? '#cf1322' : '#999';

  return (
    <svg width={width} height={height} style={{ verticalAlign: 'middle' }}>
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} />
      <circle cx={px(n - 1)} cy={py(last)} r={2} fill={color} />
    </svg>
  );
}
