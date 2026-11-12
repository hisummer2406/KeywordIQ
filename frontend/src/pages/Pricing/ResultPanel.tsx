import { Alert, Button, Card, Descriptions, Input, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import {
  formatMoney,
  formatPct,
  type CalcResult,
  type Evaluation,
  type FeeLine,
  type MarketResult,
  type OptionResult,
  type SelectionOut,
} from '@/services/pricing';

/**
 * 核价结果（受控、不自己发请求）。
 *
 * 三条展示纪律（specs/pricing-calculation、pricing-records）：
 * - 只在同一市场内比较，不给跨币种的价格排名；
 * - 「能不能算」（可用状态）与「达没达标」分开展示，不混成一个颜色；
 * - 无结果时说清是哪一类：全部不适用 / 缺少费用配置 / 资料不完整——计算失败由全局错误提示，
 *   不会伪装成「没有渠道」。
 */

export const AVAILABILITY_TEXT: Record<string, { text: string; color: string }> = {
  available: { text: '可用', color: 'green' },
  not_applicable: { text: '不适用', color: 'default' },
  needs_input: { text: '待补资料', color: 'gold' },
  missing_config: { text: '缺少费用配置', color: 'orange' },
};

export const SOLVE_TEXT: Record<string, { text: string; color: string } | undefined> = {
  met: { text: '达标', color: 'green' },
  not_met: { text: '未达标', color: 'red' },
  unsolvable: { text: '无法达到目标', color: 'red' },
};

const MARKET_STATUS: Record<string, { type: 'warning' | 'info'; title: string }> = {
  missing_config: { type: 'warning', title: '缺少费用配置，该市场无法核算' },
  needs_input: { type: 'info', title: '资料不完整，补齐后可核算' },
  all_not_applicable: { type: 'warning', title: '全部方案不适用' },
};

export interface ResultPanelProps {
  result: CalcResult;
  stale: boolean;
  /** 当前结果来自已保存版本时给出，否则为试算结果（不可选定）。 */
  savedVersionId?: number | null;
  selections?: SelectionOut[];
  canSelect?: boolean;
  priceOverrides: Record<string, string>;
  onAdjust?: (market: string, optionKey: string, price: string | null) => void;
  onSelect?: (market: string, option: OptionResult) => void;
}

function LinesTable({ ev }: { ev: Evaluation }) {
  const columns: ColumnsType<FeeLine> = [
    { title: '费用项', dataIndex: 'label', width: 160 },
    { title: '金额（¥）', dataIndex: 'amount_cny', align: 'right', width: 110, render: formatMoney },
    {
      title: '原币',
      key: 'orig',
      align: 'right',
      width: 110,
      render: (_, l) => (l.amount_orig ? `${formatMoney(l.amount_orig)} ${l.currency}` : '—'),
    },
    { title: '计费依据', dataIndex: 'basis', ellipsis: true },
    {
      title: '规则',
      key: 'rule',
      width: 160,
      render: (_, l) =>
        l.rule ? (
          <Tooltip title={l.rule.source}>
            <span>
              {l.rule.kind} v{l.rule.version}
            </span>
          </Tooltip>
        ) : (
          '—'
        ),
    },
  ];
  return (
    <div>
      <Descriptions size="small" column={4}>
        <Descriptions.Item label="售价">{formatMoney(ev.price)}</Descriptions.Item>
        <Descriptions.Item label="销售收入（¥）">{formatMoney(ev.revenue_cny)}</Descriptions.Item>
        <Descriptions.Item label="不含税收入（¥）">{formatMoney(ev.net_revenue_cny)}</Descriptions.Item>
        <Descriptions.Item label="预计单件利润（¥）">{formatMoney(ev.profit_cny)}</Descriptions.Item>
      </Descriptions>
      <Table<FeeLine>
        size="small"
        rowKey={(l) => `${l.key}-${l.label}`}
        columns={columns}
        dataSource={ev.lines}
        pagination={false}
      />
    </div>
  );
}

function AdjustBox({
  option,
  value,
  onAdjust,
  currency,
}: {
  option: OptionResult;
  value?: string;
  onAdjust?: ResultPanelProps['onAdjust'];
  currency: string;
}) {
  const [draft, setDraft] = useState(value ?? '');
  if (!onAdjust || option.availability !== 'available') return null;
  return (
    <Space wrap style={{ marginBottom: 8 }}>
      <span>调整拟售价（{currency}）</span>
      <Input
        aria-label={`${option.label} 调整价`}
        style={{ width: 140 }}
        placeholder="如 19.99"
        value={draft}
        onChange={(e) => setDraft(e.target.value.trim())}
      />
      <Button size="small" onClick={() => onAdjust(option.market, option.option_key, draft || null)}>
        按此价重算
      </Button>
      {value ? (
        <Button size="small" type="link" onClick={() => onAdjust(option.market, option.option_key, null)}>
          清除调价
        </Button>
      ) : null}
    </Space>
  );
}

function MarketCard({
  market,
  mode,
  props,
}: {
  market: MarketResult;
  mode: CalcResult['mode'];
  props: ResultPanelProps;
}) {
  const status = MARKET_STATUS[market.status];
  const selected = props.selections?.find((s) => s.market === market.market);
  const targetMode = mode === 'target_profit' || mode === 'target_margin';
  const columns: ColumnsType<OptionResult> = [
    {
      title: '方案',
      key: 'label',
      width: 220,
      render: (_, o) => (
        <Space size={4} wrap>
          <span>{o.label}</span>
          {o.recommended ? <Tag color="blue">推荐</Tag> : null}
          {selected?.option_key === o.option_key ? <Tag color="purple">已选定</Tag> : null}
          {o.transit_days ? <Typography.Text type="secondary">{o.transit_days}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'availability',
      width: 260,
      render: (_, o) => {
        const a = AVAILABILITY_TEXT[o.availability];
        return (
          <div>
            <Tag color={a.color}>{a.text}</Tag>
            {o.reasons.map((r) => (
              <div key={r}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {r}
                </Typography.Text>
              </div>
            ))}
          </div>
        );
      },
    },
    {
      title: targetMode ? `目标售价（${market.currency}）` : `售价（${market.currency}）`,
      key: 'price',
      align: 'right',
      render: (_, o) => formatMoney(targetMode ? o.target_price : o.evaluation?.price),
    },
    {
      title: `保本售价（${market.currency}）`,
      dataIndex: 'breakeven_price',
      align: 'right',
      render: formatMoney,
    },
    ...(mode === 'max_purchase'
      ? [
          {
            title: '采购价上限（¥）',
            dataIndex: 'max_purchase_cost',
            align: 'right' as const,
            render: formatMoney,
          },
        ]
      : []),
    {
      title: '预计单件利润（¥）',
      key: 'profit',
      align: 'right',
      render: (_, o) => formatMoney(o.evaluation?.profit_cny),
    },
    { title: '利润率', key: 'margin', align: 'right', render: (_, o) => formatPct(o.evaluation?.margin) },
    { title: '投资回报率', key: 'roi', align: 'right', render: (_, o) => formatPct(o.evaluation?.roi) },
    {
      title: '目标',
      key: 'target',
      render: (_, o) => {
        const s = SOLVE_TEXT[o.solve_status];
        if (!s) return '—';
        return (
          <Space size={4} orientation="vertical">
            <Tag color={s.color}>{s.text}</Tag>
            {o.target_gap_cny ? <span>差距 ¥{formatMoney(o.target_gap_cny)}</span> : null}
          </Space>
        );
      },
    },
    ...(props.canSelect && props.onSelect
      ? [
          {
            title: '操作',
            key: 'op',
            width: 90,
            render: (_: unknown, o: OptionResult) =>
              o.availability === 'available' && o.evaluation && o.solve_status !== 'unsolvable' ? (
                <Button size="small" onClick={() => props.onSelect!(market.market, o)}>
                  选定
                </Button>
              ) : null,
          },
        ]
      : []),
  ];

  return (
    <Card
      size="small"
      style={{ marginBottom: 16 }}
      title={`${market.market} · ${market.currency}`}
      extra={
        <Space size={12}>
          {market.fx_rate ? (
            <Typography.Text type="secondary">
              汇率 1 {market.currency} = {market.fx_rate} 元
            </Typography.Text>
          ) : null}
          {market.price_includes_vat === null ? null : (
            <Typography.Text type="secondary">
              {market.price_includes_vat ? '标价含增值税' : '标价不含销售税'}
            </Typography.Text>
          )}
        </Space>
      }
    >
      {status ? (
        <Alert
          type={status.type}
          showIcon
          style={{ marginBottom: market.options.length ? 12 : 0 }}
          title={status.title}
          description={market.reasons.length ? market.reasons.join('；') : undefined}
        />
      ) : null}
      {market.options.length ? (
        <Table<OptionResult>
          size="small"
          rowKey="option_key"
          columns={columns}
          dataSource={market.options}
          pagination={false}
          scroll={{ x: 1100 }}
          expandable={{
            rowExpandable: (o) => !!o.evaluation || o.availability === 'available',
            expandedRowRender: (o) => {
              const key = `${o.market}/${o.option_key}`;
              return (
                <div>
                  <AdjustBox
                    option={o}
                    value={props.priceOverrides[key]}
                    onAdjust={props.onAdjust}
                    currency={market.currency}
                  />
                  {o.adjusted ? (
                    <Alert
                      style={{ marginBottom: 8 }}
                      type={o.adjusted_met === false ? 'warning' : 'success'}
                      showIcon
                      title={`按调整价 ${formatMoney(o.adjusted.price)} ${market.currency}：预计单件利润 ¥${formatMoney(
                        o.adjusted.profit_cny,
                      )}，利润率 ${formatPct(o.adjusted.margin)}${
                        o.adjusted_met === null
                          ? ''
                          : o.adjusted_met
                            ? '，仍达标'
                            : `，未达标（差 ¥${formatMoney(o.adjusted_gap_cny)}）`
                      }`}
                    />
                  ) : null}
                  {o.evaluation ? <LinesTable ev={o.evaluation} /> : null}
                </div>
              );
            },
          }}
        />
      ) : null}
      {market.competitors.length ? (
        <div style={{ marginTop: 8 }}>
          {market.competitors.map((c, idx) => (
            <div key={idx}>
              <Typography.Text type="secondary">
                竞品 {formatMoney(c.price.price)} {c.price.currency}
                {c.price.note ? `（${c.price.note}）` : ''}：
                {c.comparable
                  ? c.gap
                    ? `推荐方案价格比竞品${c.gap.startsWith('-') ? '低' : '高'} ${formatMoney(c.gap.replace('-', ''))}`
                    : c.reason
                  : c.reason}
              </Typography.Text>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

export default function ResultPanel(props: ResultPanelProps) {
  const { result, stale } = props;
  return (
    <div>
      {stale ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="条件已修改，请重新核价"
          description="下方结果对应修改前的条件，不能按新条件保存或导出。"
        />
      ) : null}
      <div style={{ opacity: stale ? 0.55 : 1 }}>
        {result.markets.map((m) => (
          <MarketCard key={m.market} market={m} mode={result.mode} props={props} />
        ))}
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        核价日期 {result.calc_date} · 模型版本 {result.model_version} · 结果为「预计单件利润」，未含：
        {result.excluded_fees.join('、')}
      </Typography.Paragraph>
    </div>
  );
}
