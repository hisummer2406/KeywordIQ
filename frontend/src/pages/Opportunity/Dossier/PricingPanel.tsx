import { CalculatorOutlined, LinkOutlined, WarningOutlined } from '@ant-design/icons';
import { useAccess } from '@umijs/max';
import { Alert, Button, Modal, Popconfirm, Select, Table, Tag, Tooltip, Typography, theme } from 'antd';
import { useState } from 'react';
import {
  type DossierDetail,
  listLinkablePricingSheets,
  type PricingMarketBrief,
  type PricingSheetBrief,
} from '@/services/opportunityDossier';
import { formatMoney, formatRate, MODE_LABEL, PRICING_STATE } from './dossierView';

/**
 * 商业可行性（change add-opportunity-feasibility）：关联核价单的测算摘要。
 *
 * 金额全部来自核价单已保存版本里核价引擎算出的值，这里只展示、不计算。团队看得到摘要；
 * 采购成本、费用明细与输入参数只有核价单的创建人能在「定价核算」里看到。
 */

interface Props {
  detail: DossierDetail;
  currentUserId: number | null;
  onCreateSheet: () => Promise<void>;
  onOpenSheet: (sheetId: number) => void;
  onLink: (sheetId: number) => Promise<void>;
  onUnlink: (sheetId: number) => Promise<void>;
}

/** 方案来源：选定、选定过期、推荐，或只是能算出结果的一个方案（不冒充推荐） */
function SelectionTag({ m }: { m: PricingMarketBrief }) {
  if (m.selected) return <Tag color="success">已选定</Tag>;
  if (m.selection_stale)
    return (
      <Tooltip title="选定方案来自旧版本，需要在定价核算里重新选定">
        <Tag color="warning">需重新选定</Tag>
      </Tooltip>
    );
  if (m.option?.recommended)
    return (
      <Tooltip title="创建人尚未选定方案，这里显示推荐方案">
        <Tag>推荐，未选定</Tag>
      </Tooltip>
    );
  return (
    <Tooltip title="创建人尚未选定方案，也没有推荐方案，这里显示其中一个方案">
      <Tag>未选定</Tag>
    </Tooltip>
  );
}

function MarketTable({ sheet }: { sheet: PricingSheetBrief }) {
  const { token } = theme.useToken();
  const priceLabel = sheet.mode === 'fixed_price' || sheet.mode === 'max_purchase' ? '给定售价' : '目标售价';
  return (
    <Table<PricingMarketBrief>
      size="small"
      rowKey="market"
      pagination={false}
      dataSource={sheet.markets}
      columns={[
        { title: '市场', dataIndex: 'market', width: 64 },
        {
          title: '方案',
          width: 200,
          render: (_, m) =>
            m.option ? (
              <span>
                {m.option.label} <SelectionTag m={m} />
                {m.option.solve_status === 'unsolvable' && (
                  <Tooltip title={m.option.reasons.join('；')}>
                    <Tag color="error">目标无解</Tag>
                  </Tooltip>
                )}
                {m.option.solve_status === 'not_met' && <Tag color="warning">未达目标</Tag>}
                {m.option.solve_status !== 'unsolvable' && m.option.reasons.length > 0 && (
                  // 能算出结果也可能带提醒（如超出尺寸段时仓储费等未计入）：团队看不到核价明细，必须在这里露出（PR #189 复审）
                  <Tooltip title={m.option.reasons.join('；')}>
                    <Tag color="warning" icon={<WarningOutlined />}>
                      {m.option.reasons.length} 条计算提醒
                    </Tag>
                  </Tooltip>
                )}
                {m.option.adjusted && (
                  <div style={{ fontSize: 12, marginTop: 2 }}>
                    已手动调价：{formatMoney(m.option.adjusted.price, m.currency)}，利润 {formatMoney(m.option.adjusted.profit_cny, 'CNY')}，利润率{' '}
                    {formatRate(m.option.adjusted.margin)}
                    {m.option.adjusted.met === false && (
                      <Tag color="error" style={{ marginLeft: 4 }}>
                        调价后未达目标
                      </Tag>
                    )}
                    {m.option.adjusted.met === true && (
                      <Tag color="success" style={{ marginLeft: 4 }}>
                        调价后达标
                      </Tag>
                    )}
                  </div>
                )}
              </span>
            ) : (
              <Tooltip title={m.reasons.join('；')}>
                <Tag color={m.status === 'needs_input' ? 'warning' : 'default'}>{m.status_text}</Tag>
              </Tooltip>
            ),
        },
        {
          title: priceLabel,
          // 有手动调价时这里仍是引擎原始求解的售价，调后结果在「方案」列下方单独列出
          render: (_, m) => (m.option ? formatMoney(m.option.price, m.currency) : '—'),
        },
        {
          title: '预计单件利润',
          render: (_, m) =>
            m.option ? (
              <span style={{ color: Number(m.option.profit_cny) < 0 ? token.colorError : undefined }}>{formatMoney(m.option.profit_cny, 'CNY')}</span>
            ) : (
              '—'
            ),
        },
        { title: '利润率', render: (_, m) => (m.option ? formatRate(m.option.margin) : '—') },
        { title: '保本价', render: (_, m) => (m.option ? formatMoney(m.option.breakeven_price, m.currency) : '—') },
        {
          title: '采购价上限',
          render: (_, m) =>
            !m.option ? (
              '—'
            ) : m.option.max_purchase_cost === null && sheet.mode !== 'max_purchase' ? (
              <Tooltip title="只有按「售价倒推采购价上限」的方式核价时才会计算">
                <Typography.Text type="secondary">未按此方式核价</Typography.Text>
              </Tooltip>
            ) : (
              formatMoney(m.option.max_purchase_cost, 'CNY')
            ),
        },
      ]}
    />
  );
}

export default function PricingPanel({ detail, currentUserId, onCreateSheet, onOpenSheet, onLink, onUnlink }: Props) {
  const { token } = theme.useToken();
  const access = useAccess();
  const [picking, setPicking] = useState(false);
  const [options, setOptions] = useState<{ value: number; label: string }[]>([]);
  const [chosen, setChosen] = useState<number | undefined>();
  const [busy, setBusy] = useState(false);
  const { pricing } = detail;

  const openPicker = async () => {
    setChosen(undefined);
    setPicking(true);
    const r = await listLinkablePricingSheets();
    setOptions(
      r.items
        .filter((x) => x.dossier_id !== detail.dossier.id)
        .map((x) => ({
          value: x.id,
          label: `${x.product_name}${x.status === 'draft' ? '（草稿）' : ''}${x.dossier_id ? '（已挂在别的档案，选择后改挂到这里）' : ''}`,
        })),
    );
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          商业可行性
        </Typography.Title>
        <Tag color={PRICING_STATE[pricing.state].color}>{PRICING_STATE[pricing.state].text}</Tag>
        <span style={{ flex: 1 }} />
        <Button size="small" icon={<LinkOutlined />} onClick={() => openPicker().catch(() => undefined)}>
          关联我的核价单
        </Button>
        {access.canViewPricing && (
          <Button size="small" type="primary" ghost icon={<CalculatorOutlined />} loading={busy} onClick={() => run(onCreateSheet).catch(() => undefined)}>
            发起核价
          </Button>
        )}
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        金额由「定价核算」按已启用的费用规则计算，这里只展示核价单已保存的结果。采购成本和费用明细只有核价单的创建人能看到。
      </Typography.Paragraph>
      {pricing.sheets.length === 0 ? (
        <Typography.Text type="secondary">
          还没有关联核价单。按能卖的价格算一下利润和采购价上限，签「推进」前更有底。
        </Typography.Text>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {pricing.sheets.map((s) => {
            const mine = s.owner_id === currentUserId;
            return (
              <div key={s.id} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 10, padding: 12, background: token.colorBgContainer }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                  <Typography.Text strong>{s.product_name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {s.owner_name ?? '—'} 的核价单
                    {s.state === 'calculated' && ` · 第 ${s.version_no} 版 · 核价日 ${s.calc_date} · ${MODE_LABEL[s.mode ?? 'target_profit']}`}
                  </Typography.Text>
                  {s.rules_updated && (
                    <Tooltip title="这一版用到的费用规则后来被更新或停用了，金额可能已过时">
                      <Tag color="warning">规则已更新，建议重新核价</Tag>
                    </Tooltip>
                  )}
                  <span style={{ flex: 1 }} />
                  {mine ? (
                    <>
                      <Button type="link" size="small" onClick={() => onOpenSheet(s.id)}>
                        打开核价单
                      </Button>
                      <Popconfirm title="取消关联？" description="只是不再挂在这个档案下，核价单本身不受影响。" onConfirm={() => onUnlink(s.id)}>
                        <Button type="link" size="small" danger>
                          取消关联
                        </Button>
                      </Popconfirm>
                    </>
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      核价明细仅创建人可见
                    </Typography.Text>
                  )}
                </div>
                {s.state === 'draft_only' ? (
                  <Typography.Text type="secondary">尚未保存测算。{mine ? '打开核价单填好成本与尺寸后保存一个版本，这里就会显示结果。' : ''}</Typography.Text>
                ) : (
                  <>
                    <MarketTable sheet={s} />
                    {s.markets.some((m) => m.status === 'missing_config') && (
                      <Alert
                        type="info"
                        showIcon
                        style={{ marginTop: 8 }}
                        title="有市场的费用规则还没配齐（汇率、佣金、渠道等），这些市场不显示金额。规则由费用配置维护人员录入并启用。"
                      />
                    )}
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Modal
        open={picking}
        title="关联我的核价单"
        okText="关联"
        okButtonProps={{ disabled: !chosen, loading: busy }}
        onOk={() =>
          run(async () => {
            if (chosen) await onLink(chosen);
            setPicking(false);
          }).catch(() => undefined)
        }
        onCancel={() => setPicking(false)}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          只能关联你自己的核价单。关联后，团队成员能在这个档案里看到各市场的售价、预计利润和采购价上限；采购成本和费用明细仍只有你能看。
        </Typography.Paragraph>
        <Select
          style={{ width: '100%' }}
          showSearch
          optionFilterProp="label"
          placeholder={options.length ? '选择核价单' : '你还没有可关联的核价单'}
          options={options}
          value={chosen}
          onChange={setChosen}
        />
      </Modal>
    </div>
  );
}
