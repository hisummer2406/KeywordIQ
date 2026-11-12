import type { ProColumns } from '@ant-design/pro-components';
import { Popconfirm, Popover, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { SiteCodeLabel } from '@/pages/Analysis/siteLabel';
import type { CompetitorGroup, WatchRow, WatchStatus } from '@/services/competitor';
import type { SiteOption } from '@/services/upload';
import CollectGuide from './CollectGuide';

/**
 * Amazon 卖家清单的列（店铺管理 · Amazon 卖家）。
 *
 * **采集方式是「手动采集」**（#166）：数据只随运营在浏览器里用插件采集而更新，后台不会
 * 自动检查。状态三态由**服务端**判定（未采集 / 已采集 / 数据陈旧，见 `WatchStatus`），
 * 这里不再自己拿时间推算——两处各算一遍，窗口或时钟一错就是同一个卖家两种说法。
 *
 * - **未采集**：新发现数显示占位符而不是 0（0 会被读成「这家店没有新发现」），最近采集显示
 *   「未采集」。
 * - **有基线**：新发现数是真实数字（含 0，含义是「已采集范围内没有新发现」）；最近采集同时
 *   给出那次上报的条数，并注明**不代表全店总量**——上报多少取决于运营翻了几页。
 */

/**
 * 「新发现」窗口天数，与后端 `NEW_DISCOVERY_WINDOW_DAYS` 同步，只决定列标题与陈旧提示文案。
 * 陈旧与否由后端判定（`status === 'STALE'`），这里不参与判断。
 */
const NEW_DISCOVERY_WINDOW_DAYS = 7;

const STATUS_META: Record<WatchStatus, { color: string; text: string; hint: string }> = {
  NOT_COLLECTED: {
    color: 'default',
    text: '未采集',
    hint: '收藏只代表加入关注清单。打开这个卖家的商品列表、用插件采集一次后才会有数据。',
  },
  COLLECTED: {
    color: 'green',
    text: '已采集',
    hint: `最近 ${NEW_DISCOVERY_WINDOW_DAYS} 天内有过人工采集。`,
  },
  STALE: {
    color: 'gold',
    text: '数据陈旧',
    hint: `超过 ${NEW_DISCOVERY_WINDOW_DAYS} 天没有新的采集——「0 新发现」可能只是因为没有采集，不代表这家店没有新品。`,
  },
};

/** 卖家商品列表页模板链接。只用 seller_id/site 现拼，不回存/复用原始 URL 里的会话签名参数。 */
export function sellerLink(row: Pick<WatchRow, 'seller_id' | 'site'>, domain?: string) {
  if (!domain) return null;
  return `https://www.${domain}/s?i=merchant-items&me=${row.seller_id}&s=date-desc-rank`;
}

/** 卖家显示名：店名取不到时回落 seller_id。**不拿私有备注顶替**——那是用户自己的话，不是店名。 */
export function sellerDisplayName(row: Pick<WatchRow, 'seller_id' | 'seller_name'>) {
  return row.seller_name || row.seller_id;
}

export function buildWatchColumns(
  sites: SiteOption[],
  groups: CompetitorGroup[],
  onChangeGroup: (watchId: number, groupId: number | null) => void,
  onRemove: (watchId: number) => void,
  onOpenAsins: (row: WatchRow) => void,
  onOpenDiscovery?: (row: WatchRow) => void,
): ProColumns<WatchRow>[] {
  const domainOf = (site: string) => sites.find((s) => s.code === site)?.domain;

  return [
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      align: 'center',
      render: (_, row) => {
        // 旧后端或未知取值一律按「未采集」呈现：宁可多提示一次去采集，也不冒充已有数据。
        const meta = STATUS_META[row.status] ?? STATUS_META.NOT_COLLECTED;
        return (
          <Tooltip title={meta.hint}>
            <Tag color={meta.color}>{meta.text}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '采集方式',
      key: 'collect_mode',
      width: 100,
      align: 'center',
      render: () => (
        <Tooltip title="随你在浏览器里用插件采集更新，后台不会自动检查">
          <Typography.Text>手动采集</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '卖家',
      dataIndex: 'seller_id',
      width: 220,
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <a onClick={() => onOpenAsins(row)}>{sellerDisplayName(row)}</a>
          {row.seller_name ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.seller_id}
            </Typography.Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '站点',
      dataIndex: 'site',
      width: 90,
      render: (_, row) => <SiteCodeLabel code={row.site} />,
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
          onChange={(v) => onChangeGroup(row.watch_id, v ?? null)}
          onClear={() => onChangeGroup(row.watch_id, null)}
          options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
        />
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 80,
      align: 'center',
      tooltip: '重复收藏/导入的次数,数字越大说明这个卖家在原始清单里被反复标记',
    },
    {
      title: `近 ${NEW_DISCOVERY_WINDOW_DAYS} 日新发现`,
      dataIndex: 'new_discoveries_7d',
      width: 130,
      align: 'center',
      render: (_, row) =>
        row.new_discoveries_7d === null ? (
          <Typography.Text type="secondary" title="该卖家还没有被采集过，这个指标还没有数据来源">
            —
          </Typography.Text>
        ) : (
          row.new_discoveries_7d
        ),
    },
    {
      title: '最近采集',
      dataIndex: 'last_reported_at',
      width: 200,
      render: (_, row) => {
        if (!row.last_reported_at) {
          return <Typography.Text type="secondary">未采集</Typography.Text>;
        }
        const time = new Date(row.last_reported_at).toLocaleString('zh-CN');
        return (
          <Space orientation="vertical" size={0}>
            <Typography.Text type={row.status === 'STALE' ? 'warning' : undefined}>{time}</Typography.Text>
            {row.last_report_item_count !== null ? (
              <Tooltip title="这是那一次上报见到的条数，取决于翻了几页；没有完整性依据，不是这家店的在售总数">
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  上报 {row.last_report_item_count} 条（不代表全店总量）
                </Typography.Text>
              </Tooltip>
            ) : null}
          </Space>
        );
      },
    },
    {
      title: '关注时间',
      dataIndex: 'created_at',
      valueType: 'dateTime',
      width: 160,
    },
    {
      title: '备注',
      dataIndex: 'note',
      ellipsis: true,
      render: (_, row) => row.note || <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 260,
      render: (_, row) => {
        const link = sellerLink(row, domainOf(row.site));
        return [
          onOpenDiscovery ? (
            <a key="discovery" onClick={() => onOpenDiscovery(row)}>
              看新商品
            </a>
          ) : null,
          link ? (
            <a key="open" href={link} target="_blank" rel="noreferrer">
              打开商品列表
            </a>
          ) : null,
          <Popover key="guide" title="如何采集" content={<CollectGuide />} trigger="click">
            <a>采集说明</a>
          </Popover>,
          <Popconfirm
            key="remove"
            title="取消关注这个卖家?"
            okText="取消关注"
            okButtonProps={{ danger: true }}
            cancelText="再想想"
            onConfirm={() => onRemove(row.watch_id)}
          >
            <a>取消关注</a>
          </Popconfirm>,
        ];
      },
    },
  ];
}
