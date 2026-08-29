import type { ProColumns } from '@ant-design/pro-components';
import { Typography } from 'antd';
import TrendSparkline from '@/components/TrendSparkline';
import type { GrowthRow } from '@/services/keyword';
import { formatPercent } from './criteria';

/**
 * 榜单列。
 *
 * 只有三列挂 sorter，与后端白名单一一对应——多挂一列的表现是点了表头得到 422，
 * 整张表报错。
 */
export function buildColumns(): ProColumns<GrowthRow>[] {
  return [
    {
      title: '关键词',
      dataIndex: 'keyword',
      width: 220,
      ellipsis: true,
    },
    {
      // 起止两列并排：只看净增的话，「10 万→6 万」与「5 万→1 万」在界面上一模一样，
      // 而这两件事在选品上完全不同。
      title: '起始排名',
      dataIndex: 'start_ranking',
      width: 110,
      render: (_, r) => r.start_ranking.toLocaleString(),
    },
    {
      title: '当前排名',
      dataIndex: 'end_ranking',
      width: 110,
      sorter: true,
      render: (_, r) => r.end_ranking.toLocaleString(),
    },
    {
      // 名次前进用绿色，与分析屏「负数=上升」的配色一致（那边负号表示上升）。
      // 这里的数值本身是正数，因为它是「前进了多少名」，符号含义不同——所以列名叫
      // 「累计前进」而不是「排名变化」，避免用户拿这个数去回填分析屏的筛选。
      title: '累计前进名次',
      dataIndex: 'rank_gain',
      width: 130,
      sorter: true,
      defaultSortOrder: 'descend',
      render: (_, r) => (
        <Typography.Text style={{ color: '#52c41a' }}>
          ↑ {r.rank_gain.toLocaleString()}
        </Typography.Text>
      ),
    },
    {
      title: '最低单日增幅',
      dataIndex: 'min_growth_rate',
      width: 130,
      sorter: true,
      render: (_, r) => formatPercent(r.min_growth_rate),
    },
    {
      title: '排名走势',
      dataIndex: 'trend',
      width: 190,
      render: (_, r) => <TrendSparkline values={r.trend} dates={r.trend_dates} />,
    },
  ];
}
