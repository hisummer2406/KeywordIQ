import type { ProColumns } from '@ant-design/pro-components';
import { Popconfirm, Select, Tag, Typography } from 'antd';
import { siteCodeToFlag } from '@/pages/Analysis/siteLabel';
import type { CompetitorGroup, WatchRow, WatchStatus } from '@/services/competitor';
import type { SiteOption } from '@/services/upload';

/**
 * 主表格列。**没有指标列**——本变更（P0）没有数据获取层，`baseline_at` 恒为 NULL，
 * 唯一能展示的是关注关系本身（卖家、站点、分组、优先级、备注、关注时间）与占位的
 * 「近 7 日上新数」列。占位符不能是 0 或空白：0 会被读成"没有新品"，占位符才准确
 * 表达"这个指标还没有数据来源"（spec.md「关注状态展示待建立基线」场景）。
 */

/**
 * 状态**两态**，跟着 `WatchStatus` 走。这里曾有第三项「已暂停」，删掉是因为它取不到值——
 * 详见 `services/competitor.ts` 的 `WatchStatus` 注释。
 */
const STATUS_META: Record<WatchStatus, { color: string; text: string }> = {
  PENDING_BASELINE: { color: 'gold', text: '待建立基线' },
  MONITORING: { color: 'green', text: '监控中' },
};

/** 卖家收藏页模板链接。只用 seller_id/site 现拼,不回存/复用原始 URL 里的会话签名参数。 */
function sellerLink(row: WatchRow, domain?: string) {
  if (!domain) return null;
  return `https://www.${domain}/s?i=merchant-items&me=${row.seller_id}&s=date-desc-rank`;
}

export function buildWatchColumns(
  sites: SiteOption[],
  groups: CompetitorGroup[],
  onChangeGroup: (watchId: number, groupId: number | null) => void,
  onRemove: (watchId: number) => void,
): ProColumns<WatchRow>[] {
  const domainOf = (site: string) => sites.find((s) => s.code === site)?.domain;

  return [
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      align: 'center',
      render: (_, row) => {
        const meta = STATUS_META[row.status] ?? STATUS_META.PENDING_BASELINE;
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: '卖家',
      dataIndex: 'seller_id',
      width: 220,
      render: (_, row) => {
        const link = sellerLink(row, domainOf(row.site));
        return link ? (
          <a href={link} target="_blank" rel="noreferrer">
            {row.seller_id}
          </a>
        ) : (
          row.seller_id
        );
      },
    },
    {
      title: '站点',
      dataIndex: 'site',
      width: 90,
      render: (_, row) => (
        <span>
          {siteCodeToFlag(row.site)} {row.site}
        </span>
      ),
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
      title: '近 7 日上新数',
      dataIndex: 'new_arrivals',
      width: 130,
      align: 'center',
      render: () => (
        <Typography.Text type="secondary" title="数据获取层尚未实现,这个指标还没有数据来源">
          待接入
        </Typography.Text>
      ),
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
      width: 90,
      render: (_, row) => [
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
      ],
    },
  ];
}
