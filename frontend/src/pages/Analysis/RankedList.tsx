import { Typography } from 'antd';

/**
 * 按名次竖排的三个值（品牌 / 类目）。
 *
 * 名次由**位置**承载，所以缺失的名次留空而不是把后面的顶上来——顶上来之后
 * "第二品牌"这个说法就不再成立，而表格里看不出发生过顶替。
 *
 * 也不把三个值挤成逗号串：那样名次信息直接消失，而名次是这些值的全部意义。
 */
export default function RankedList({
  values,
  rows = 2,
}: {
  values: (string | null)[];
  /** 每个值最多显示几行，超出截断并由悬停提示兜底。类目传 1（不换行），品牌默认 2。 */
  rows?: number;
}) {
  if (!values.some(Boolean)) return <span style={{ color: '#ccc' }}>—</span>;

  return (
    // **不居中**：块宽等于该行最长的那个值，各行不同；居中之后每行起点都不一样，
    // 顺着列往下扫就是左右横跳。列存在的意义是纵向对照，起点必须固定。
    <div style={{ lineHeight: 1.5, textAlign: 'left' }}>
      {values.map((v, i) => (
        // alignItems: flex-start —— 值换行后序号要跟第一行齐，否则会垂直居中飘到中间
        <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
          <Typography.Text type="secondary" style={{ fontSize: 11, width: 14, flexShrink: 0 }}>
            {i + 1}
          </Typography.Text>
          {/*
            用 Paragraph 而不是 Text：多行截断（rows）是 Paragraph 的能力，
            Text 的类型里明确 Omit 掉了 rows。marginBottom 要显式清零，
            Paragraph 默认带下边距，三个值叠起来会把格子撑高。

            行数由调用方给：品牌名可以很长（`MGM METRO GOLDWYN MAYER TRADE MARK ARS
            GRATIA ARTIS`），死限一行几乎全被吃掉，故默认两行；类目名相对规整、且一格三个
            值全换行会明显撑高行，故按列传 1。完全放开则不可取——一格三个值全展开会把行高
            顶到卡牌的一倍以上，而这两列只是辅助信息，不该主导行高。
          */}
          <Typography.Paragraph
            ellipsis={{ rows, tooltip: v ?? undefined }}
            style={{ fontSize: 12, marginBottom: 0, color: v ? undefined : '#ccc' }}
          >
            {v || '—'}
          </Typography.Paragraph>
        </div>
      ))}
    </div>
  );
}
