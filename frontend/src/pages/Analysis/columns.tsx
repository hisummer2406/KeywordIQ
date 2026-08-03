import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';
import type { AnalysisRow, View } from '@/services/analysis';
import ShareBar from '@/components/ShareBar';
import TrendSparkline from '@/components/TrendSparkline';

/**
 * 结果表格的列定义。
 *
 * 列上不再挂 `valueType` / `fieldProps` 的搜索配置——筛选已全部移入过滤区，
 * 同一个条件出现在两处会让用户无法判断哪一处生效。
 */

/** 排名变化：数值变小 = 名次上升。0 与空各有各的呈现，不要混。 */
function renderChange(v: number | null | undefined) {
  if (v === null || v === undefined) return <span style={{ color: '#ccc' }}>—</span>;
  if (v === 0) return <span style={{ color: '#999' }}>0</span>;
  const up = v < 0;
  return (
    <span style={{ color: up ? '#3f8600' : '#cf1322' }}>
      {up ? '↑' : '↓'} {Math.abs(v)}
    </span>
  );
}

export function buildColumns(view: View): ProColumns<AnalysisRow>[] {
  return [
    { title: '排名', dataIndex: 'current_ranking', width: 80 },
    // 关键词与商品标题是两列「要读内容」的列，其余自适应列给定宽，把剩余横向空间
    // 让给这两列。
    { title: '关键词', dataIndex: 'keyword', width: 220, ellipsis: true, copyable: true },
    {
      title: '7天走势',
      dataIndex: 'trend',
      width: 190,
      hideInTable: view !== 'DAY',
      render: (_, r) => <TrendSparkline values={r.trend} dates={r.trend_dates} />,
    },
    {
      title: '排名变化',
      dataIndex: 'ranking_change',
      width: 100,
      render: (_, r) => renderChange(r.ranking_change),
    },
    {
      title: '周排名变化',
      dataIndex: 'week_change',
      width: 100,
      hideInTable: view !== 'DAY',
      render: (_, r) => renderChange(r.week_change),
    },
    {
      title: '新词',
      dataIndex: 'is_new',
      width: 80,
      render: (_, r) => (r.is_new ? <Tag color="gold">新词</Tag> : '—'),
    },
    { title: '品牌', dataIndex: 'top1_brand', width: 130, ellipsis: true },
    {
      title: '类目',
      dataIndex: 'top1_category',
      width: 150,
      ellipsis: true,
      render: (_, r) => r.top1_category || '—',
    },
    { title: 'ASIN', dataIndex: 'top1_asin', width: 120, copyable: true },
    // 不给 width：让它吃掉所有剩余横向空间。ellipsis 仍保留——再宽也有超长标题，
    // 截断时 antd 会挂 title 属性，悬停可看全文。
    { title: '商品标题', dataIndex: 'top1_title', ellipsis: true },
    {
      title: '点击份额',
      dataIndex: 'top1_click_share',
      width: 110,
      render: (_, r) => <ShareBar value={r.top1_click_share} />,
    },
    {
      title: '转化份额',
      dataIndex: 'top1_conversion_share',
      width: 110,
      render: (_, r) => <ShareBar value={r.top1_conversion_share} />,
    },
  ];
}
