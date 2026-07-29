/**
 * 份额单元格：数值 + 背后的颜色条（条宽 = 份额%）。份额越高颜色越突出，
 * 一眼看出高低。点击份额/转化份额通用。
 */
export default function ShareBar({ value }: { value?: number }) {
  const v = value ?? 0;
  const pct = Math.max(0, Math.min(100, v));
  const color = v >= 30 ? '#1677ff' : v >= 10 ? '#52c41a' : '#faad14';
  return (
    <div style={{ position: 'relative', minWidth: 72, height: 18, lineHeight: '18px' }}>
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          bottom: 0,
          width: `${pct}%`,
          background: `${color}26`,
          borderRadius: 3,
        }}
      />
      <span
        style={{
          position: 'relative',
          paddingLeft: 4,
          color,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {v}%
      </span>
    </div>
  );
}
