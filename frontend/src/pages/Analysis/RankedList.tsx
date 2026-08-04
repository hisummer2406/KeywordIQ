import { Typography } from 'antd';

/**
 * 按名次竖排的三个值（品牌 / 类目）。
 *
 * 名次由**位置**承载，所以缺失的名次留空而不是把后面的顶上来——顶上来之后
 * "第二品牌"这个说法就不再成立，而表格里看不出发生过顶替。
 *
 * 也不把三个值挤成逗号串：那样名次信息直接消失，而名次是这些值的全部意义。
 */
export default function RankedList({ values }: { values: (string | null)[] }) {
  if (!values.some(Boolean)) return <span style={{ color: '#ccc' }}>—</span>;

  return (
    <div style={{ lineHeight: 1.5 }}>
      {values.map((v, i) => (
        <div key={i} style={{ display: 'flex', gap: 6 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11, width: 14, flexShrink: 0 }}>
            {i + 1}
          </Typography.Text>
          <Typography.Text
            ellipsis={{ tooltip: v ?? undefined }}
            style={{ fontSize: 12, color: v ? undefined : '#ccc' }}
          >
            {v || '—'}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}
