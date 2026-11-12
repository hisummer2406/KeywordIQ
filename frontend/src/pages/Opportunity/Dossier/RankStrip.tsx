import { theme } from 'antd';
import type { SeriesPoint } from '@/services/opportunityDossier';
import { formatRank, POINT_STATUS } from './dossierView';

/**
 * 逐期排名条：每一期占一格，**缺榜的期次也占位**。
 *
 * 不用通用的折线迷你图：那种图只画有值的点，断续在榜的词（58 期里只在榜 20 期）会被画成一条
 * 连续的线，看不出中间缺了多少期。这里在榜的期画点并连线，没在榜的期在底部画灰色短线，
 * 报表没导入的期留空——缺榜、未导入、在榜三者一眼分得开。
 *
 * 纵轴取对数并翻转：排名数字越小越靠上；排名跨度常达两个数量级，线性轴会把好排名压成一条直线。
 */
export default function RankStrip({ points, width = 360, height = 56 }: { points: SeriesPoint[]; width?: number; height?: number }) {
  const { token } = theme.useToken();
  if (!points.length) return null;
  const ranked = points.filter((p) => p[1] === 'R' && p[2] !== null) as [string, 'R', number][];
  const pad = 6;
  const base = height - 8;
  const n = points.length;
  const x = (i: number) => (n === 1 ? width / 2 : pad + (i / (n - 1)) * (width - pad * 2));
  const logs = ranked.map((p) => Math.log10(Math.max(1, p[2])));
  const lo = Math.min(...logs);
  const hi = Math.max(...logs);
  const y = (rank: number) => (hi === lo ? (pad + base - 6) / 2 : pad + ((Math.log10(Math.max(1, rank)) - lo) / (hi - lo)) * (base - 6 - pad));

  // 只连接相邻两期都在榜的点：中间缺榜就断开，不画出并不存在的连续走势
  const segments: string[] = [];
  let run: string[] = [];
  points.forEach((p, i) => {
    if (p[1] === 'R' && p[2] !== null) run.push(`${x(i).toFixed(1)},${y(p[2]).toFixed(1)}`);
    else {
      if (run.length > 1) segments.push(run.join(' '));
      run = [];
    }
  });
  if (run.length > 1) segments.push(run.join(' '));

  return (
    <svg width={width} height={height} role="img" aria-label="逐期排名">
      {segments.map((s) => (
        <polyline key={s} points={s} fill="none" stroke={token.colorPrimary} strokeWidth={1.5} />
      ))}
      {points.map((p, i) => {
        const [date, status, rank] = p;
        const label = status === 'R' ? `${date} 排名 ${formatRank(rank)}` : `${date} ${POINT_STATUS[status]}`;
        if (status === 'R' && rank !== null) {
          return (
            <circle key={date} cx={x(i)} cy={y(rank)} r={2.2} fill={token.colorPrimary}>
              <title>{label}</title>
            </circle>
          );
        }
        if (status === 'X' || status === 'O') {
          return (
            <rect key={date} x={x(i) - 1.5} y={base} width={3} height={4} fill="none" stroke={token.colorBorder} strokeWidth={0.6}>
              <title>{label}</title>
            </rect>
          );
        }
        return (
          <rect key={date} x={x(i) - 1.5} y={base} width={3} height={4} fill={status === 'D' ? token.colorWarning : token.colorTextQuaternary}>
            <title>{label}</title>
          </rect>
        );
      })}
    </svg>
  );
}
