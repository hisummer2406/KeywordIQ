import type { ProColumns } from '@ant-design/pro-components';
import { Popconfirm, Select, Space, Tooltip, Typography } from 'antd';
import type { CompetitorGroup, StoreRow } from '@/services/competitor';
import { renderRankHealth, renderStoreStatus } from './storeStatus';

/**
 * 独立站清单的列。与 Amazon 卖家那张表**刻意不共用列定义**——两类对象能拿到的字段
 * 几乎不重叠（这边有上架时间与币种、没有销量绝对值；那边有 BSR 与评价数、没有上架
 * 时间），硬塞进一张表会让一半的列对另一类恒为空。
 *
 * 「近 7 日上架」这一列在这里是**真实数字（含 0）**，不是 Amazon 侧那种占位符——
 * 独立站有真实数据源，显示占位符反而成了谎报。两类行的这一列必须能被用户一眼分辨，
 * 否则「这家确实没上新」与「这家还没有数据来源」会长得一模一样。
 */

/** 币种符号只做常见几种，其余直接显示币种码——猜错符号比不猜更糟。 */
const CURRENCY_SYMBOL: Record<string, string> = {
  USD: '$',
  GBP: '£',
  EUR: '€',
  JPY: '¥',
  CAD: 'CA$',
  AUD: 'A$',
};

export function formatMoney(amount: string | null, currency: string | null): string {
  if (amount === null || amount === undefined || amount === '') return '—';
  if (!currency) return amount; // 币种未知时只显示数字，不臆造符号
  const symbol = CURRENCY_SYMBOL[currency];
  return symbol ? `${symbol}${amount}` : `${amount} ${currency}`;
}

export function buildStoreColumns(
  groups: CompetitorGroup[],
  onChangeGroup: (domain: string, groupId: number | null) => void,
  onRemove: (domain: string) => void,
  onOpenProducts: (row: StoreRow) => void,
  onOpenDiscovery?: (row: StoreRow) => void,
): ProColumns<StoreRow>[] {
  return [
    {
      title: '状态',
      dataIndex: 'status',
      width: 160,
      render: (_, row) => (
        <Space size={4} wrap>
          {renderStoreStatus(row)}
          {renderRankHealth(row)}
        </Space>
      ),
    },
    {
      title: '采集方式',
      key: 'collect_mode',
      width: 100,
      align: 'center',
      render: () => (
        <Tooltip title="后台按固定间隔自动检查商品目录，不需要你操作">
          <Typography.Text>自动检查</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '站点',
      dataIndex: 'domain',
      width: 220,
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <Typography.Link href={`https://${row.domain}`} target="_blank" rel="noreferrer">
            {row.name || row.domain}
          </Typography.Link>
          {row.name ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.domain}
            </Typography.Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '币种',
      dataIndex: 'currency',
      width: 80,
      render: (_, row) => row.currency || '—',
    },
    {
      title: '分组',
      dataIndex: 'group_id',
      width: 180,
      render: (_, row) => (
        <Select
          size="small"
          style={{ width: '100%' }}
          value={row.group_id ?? undefined}
          placeholder="未分组"
          allowClear
          showSearch
          optionFilterProp="label"
          options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
          onChange={(value) => onChangeGroup(row.domain, value ?? null)}
        />
      ),
    },
    {
      title: '在售商品',
      dataIndex: 'product_count',
      width: 110,
      align: 'right',
      render: (_, row) =>
        row.status === 'NEVER_FETCHED' ? (
          <Typography.Text type="secondary">待拉取</Typography.Text>
        ) : (
          <Typography.Link onClick={() => onOpenProducts(row)}>{row.product_count}</Typography.Link>
        ),
    },
    {
      title: '近 7 日上架',
      dataIndex: 'new_in_7d',
      width: 120,
      align: 'right',
      // 这里显示真实的 0，不显示占位符——独立站有真实数据源，0 就是「这周没上新」。
      // Amazon 卖家那张表相反（没有数据源，必须占位符），两者不可混淆。
      render: (_, row) =>
        row.status === 'NEVER_FETCHED' ? (
          <Typography.Text type="secondary">待拉取</Typography.Text>
        ) : (
          row.new_in_7d
        ),
    },
    {
      title: '上次拉取',
      dataIndex: 'last_fetch_at',
      width: 170,
      render: (_, row) =>
        row.last_fetch_at ? (
          new Date(row.last_fetch_at).toLocaleString('zh-CN')
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: '备注',
      dataIndex: 'note',
      ellipsis: true,
      render: (_, row) => row.note || '—',
    },
    {
      title: '操作',
      valueType: 'option',
      width: 170,
      render: (_, row) => [
        onOpenDiscovery ? (
          <Typography.Link key="discovery" onClick={() => onOpenDiscovery(row)}>
            看新商品
          </Typography.Link>
        ) : null,
        <Popconfirm
          key="remove"
          title="取消关注"
          description="只取消你的关注，已抓到的数据会保留（别人可能还在盯这家店）。"
          onConfirm={() => onRemove(row.domain)}
        >
          <Typography.Link type="danger">取消关注</Typography.Link>
        </Popconfirm>,
      ],
    },
  ];
}
