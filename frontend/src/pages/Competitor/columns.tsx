import type { ProColumns } from '@ant-design/pro-components';
import { Popconfirm, Select, Tag, Tooltip, Typography } from 'antd';
import { siteCodeToFlag } from '@/pages/Analysis/siteLabel';
import type { CompetitorGroup, WatchRow, WatchStatus } from '@/services/competitor';
import type { SiteOption } from '@/services/upload';

/**
 * 主表格列。指标列（近 7 日新发现 / 最近采集于）按 `WatchRow` 的两个字段渲染，两态：
 *
 * - **无基线**（`status === 'PENDING_BASELINE'`）：新发现数显示占位符，最近采集于显示
 *   「未采集」。此时后端也确实返回 `null`，不是前端猜的——两处状态判据保持一致。
 * - **有基线**：新发现数显示真实数字（含 0），最近采集于显示时刻并在陈旧（超过窗口天数
 *   没有新上报）时追加提示。「很久没采过」与「确实没有新发现」不能长得一样。
 */

/**
 * 「新发现」窗口天数，与后端 `NEW_DISCOVERY_WINDOW_DAYS` 同步：
 * 1. 决定列标题「近 7 日新发现」里的数字；
 * 2. 决定陈旧判据——`last_reported_at` 早于这个窗口，意味着窗口期内**根本没有观测**，
 *    此时的「0」连"已采集范围内没有新发现"都算不上，必须提示。
 *
 * 后端没有把这个数字通过接口下发（它是代码常量不是运行时配置），所以这里手动保持同步；
 * 改了后端那个常量，这里也要改，且列标题文案要一起改。
 */
const NEW_DISCOVERY_WINDOW_DAYS = 7;

function isStale(lastReportedAt: string | null): boolean {
  if (!lastReportedAt) return false; // 无基线时不适用"陈旧"，那是另一种占位
  const ageMs = Date.now() - new Date(lastReportedAt).getTime();
  return ageMs > NEW_DISCOVERY_WINDOW_DAYS * 24 * 60 * 60 * 1000;
}

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
      title: `近 ${NEW_DISCOVERY_WINDOW_DAYS} 日新发现`,
      dataIndex: 'new_discoveries_7d',
      width: 130,
      align: 'center',
      render: (_, row) =>
        row.new_discoveries_7d === null ? (
          <Typography.Text type="secondary" title="该卖家还没有被采集过，这个指标还没有数据来源">
            待建立基线
          </Typography.Text>
        ) : (
          row.new_discoveries_7d
        ),
    },
    {
      title: '最近采集于',
      dataIndex: 'last_reported_at',
      width: 170,
      render: (_, row) => {
        if (!row.last_reported_at) {
          return <Typography.Text type="secondary">未采集</Typography.Text>;
        }
        const text = new Date(row.last_reported_at).toLocaleString('zh-CN');
        if (!isStale(row.last_reported_at)) return text;
        return (
          <Tooltip title={`已超过 ${NEW_DISCOVERY_WINDOW_DAYS} 天没有新的上报，「新发现」数可能不反映最新情况`}>
            <Typography.Text type="warning">{text}</Typography.Text>
          </Tooltip>
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
