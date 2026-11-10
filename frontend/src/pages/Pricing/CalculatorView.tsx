import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  calculatePricing,
  createSheet,
  exportVersion,
  getSheet,
  getVersion,
  newClientToken,
  saveVersion,
  selectOption,
  updateSheet,
  type CalcResult,
  type OptionResult,
  type PricingInput,
  type PricingMeta,
  type Sheet,
} from '@/services/pricing';
import {
  DEFAULT_FORM_VALUES,
  fromPricingInput,
  sameInput,
  toPricingInput,
  type PricingFormValues,
} from './formInput';
import ResultPanel from './ResultPanel';

export interface OpenTarget {
  sheetId: number;
  versionId?: number | null;
  nonce: number;
}

interface Props {
  meta: PricingMeta;
  openTarget?: OpenTarget | null;
  onSheetChanged?: () => void;
  /** 当前核价单变化时同步到地址（保存后可刷新、可收藏；新建时清空）。 */
  onLocate?: (sheetId: number | null, versionId?: number | null) => void;
}

const MODE_OPTIONS = [
  { label: '按目标利润定价', value: 'target_profit' },
  { label: '按目标利润率定价', value: 'target_margin' },
  { label: '按售价算利润', value: 'fixed_price' },
  { label: '倒推采购价上限', value: 'max_purchase' },
];

const num = (props: Record<string, unknown> = {}) => (
  <InputNumber stringMode min="0" style={{ width: '100%' }} controls={false} {...props} />
);

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function CalculatorView({ meta, openTarget, onSheetChanged, onLocate }: Props) {
  const [form] = Form.useForm<PricingFormValues>();
  const values = Form.useWatch([], form) as PricingFormValues | undefined;
  const [result, setResult] = useState<CalcResult | null>(null);
  const [lastInput, setLastInput] = useState<PricingInput | null>(null);
  const [priceOverrides, setPriceOverrides] = useState<Record<string, string>>({});
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [versionId, setVersionId] = useState<number | null>(null);
  const [busy, setBusy] = useState<'calc' | 'save' | 'draft' | 'export' | null>(null);
  const [selecting, setSelecting] = useState<{ market: string; option: OptionResult } | null>(null);
  const [selectNote, setSelectNote] = useState('');

  const currencies = useMemo(
    () => Object.fromEntries(meta.markets.map((m) => [m.code, m.currency])),
    [meta.markets],
  );
  const kindName = useMemo(
    () => Object.fromEntries(meta.rule_kinds.map((k) => [k.key, k.name])),
    [meta.rule_kinds],
  );

  const currentInput = useMemo(
    () => (values?.markets ? toPricingInput({ ...DEFAULT_FORM_VALUES, ...values }, currencies, priceOverrides) : null),
    [values, currencies, priceOverrides],
  );
  const stale = !!result && !sameInput(currentInput, lastInput);
  const fromSavedVersion = !!sheet && versionId !== null && versionId === sheet.current_version_id && !stale;

  const loadSheet = useCallback(
    async (sheetId: number, wantVersion?: number | null) => {
      const s = await getSheet(sheetId);
      setSheet(s);
      const metaFields = {
        product_name: s.product_name,
        sku: s.sku ?? '',
        asin: s.asin ?? '',
        source_url: s.source_url ?? '',
      };
      const vid = wantVersion ?? s.current_version_id;
      const ver = vid ? await getVersion(sheetId, vid) : null;
      // 指定了版本：表单与结果都取该版本。没指定：表单取最新草稿（保存版本时会同步更新），
      // 结果取最新版本——二者不一致时页面自动标「条件已修改」，不会把草稿悄悄覆盖掉。
      const formSource = wantVersion && ver ? ver.input : s.draft_input ?? ver?.input;
      const { values: fv, priceOverrides: po } = fromPricingInput(formSource);
      form.setFieldsValue({ ...fv, ...metaFields });
      setPriceOverrides(po);
      if (ver) {
        const vv = fromPricingInput(ver.input);
        setResult(ver.result);
        setLastInput(toPricingInput(vv.values, currencies, vv.priceOverrides));
        setVersionId(ver.id);
      } else {
        setResult(null);
        setLastInput(null);
        setVersionId(null);
      }
    },
    [form, currencies],
  );

  useEffect(() => {
    if (openTarget) {
      loadSheet(openTarget.sheetId, openTarget.versionId).catch(() => undefined);
    }
  }, [openTarget, loadSheet]);

  const runCalc = async (overrides = priceOverrides) => {
    const v = await form.validateFields();
    const input = toPricingInput({ ...DEFAULT_FORM_VALUES, ...v }, currencies, overrides);
    setBusy('calc');
    try {
      const resp = await calculatePricing(input);
      setResult(resp.result);
      setLastInput(input);
      setVersionId(null);
    } finally {
      setBusy(null);
    }
  };

  const onAdjust = (market: string, optionKey: string, price: string | null) => {
    const next = { ...priceOverrides };
    const key = `${market}/${optionKey}`;
    if (price) next[key] = price;
    else delete next[key];
    setPriceOverrides(next);
    runCalc(next).catch(() => undefined);
  };

  const sheetMeta = (v: PricingFormValues) => ({
    product_name: (v.product_name ?? '').trim(),
    sku: v.sku?.trim() || null,
    asin: v.asin?.trim() || null,
    source_url: v.source_url?.trim() || null,
  });

  const ensureSheet = async (v: PricingFormValues, draft: PricingInput): Promise<Sheet> => {
    const m = sheetMeta(v);
    if (!sheet) return createSheet({ ...m, draft_input: draft });
    const changed =
      m.product_name !== sheet.product_name || m.sku !== sheet.sku || m.asin !== sheet.asin ||
      m.source_url !== sheet.source_url;
    if (!changed) return sheet;
    return updateSheet(sheet.id, { ...m, lock_version: sheet.lock_version });
  };

  const onSaveDraft = async () => {
    const v = await form.validateFields(['product_name']).then(() => form.getFieldsValue(true));
    const draft = toPricingInput({ ...DEFAULT_FORM_VALUES, ...v }, currencies, priceOverrides);
    setBusy('draft');
    try {
      let s = await ensureSheet(v, draft);
      s = await updateSheet(s.id, { lock_version: s.lock_version, draft_input: draft });
      setSheet(s);
      message.success('草稿已保存');
      onLocate?.(s.id);
      onSheetChanged?.();
    } finally {
      setBusy(null);
    }
  };

  const onSave = async () => {
    if (!result || stale || !lastInput) return;
    const v = await form.validateFields();
    setBusy('save');
    const token = newClientToken();
    try {
      const s = await ensureSheet(v, lastInput);
      const ver = await saveVersion(s.id, { lock_version: s.lock_version, client_token: token, input: lastInput });
      const fresh = await getSheet(s.id);
      setSheet(fresh);
      setResult(ver.result);
      setVersionId(ver.id);
      message.success(`已保存为 v${ver.version_no}（服务端已按输入重新计算）`);
      onLocate?.(fresh.id, ver.id);
      onSheetChanged?.();
    } finally {
      setBusy(null);
    }
  };

  const onExport = async () => {
    if (!sheet || versionId === null) return;
    setBusy('export');
    try {
      const blob = await exportVersion(sheet.id, versionId);
      const vno = sheet.versions.find((x) => x.id === versionId)?.version_no ?? versionId;
      downloadBlob(blob, `核价_${sheet.product_name}_v${vno}.csv`);
    } finally {
      setBusy(null);
    }
  };

  const confirmSelect = async () => {
    if (!selecting || !sheet || versionId === null) return;
    const s = await selectOption(sheet.id, selecting.market, {
      version_id: versionId,
      option_key: selecting.option.option_key,
      note: selectNote || null,
    });
    setSheet(s);
    setSelecting(null);
    setSelectNote('');
    message.success(`${selecting.market} 已选定 ${selecting.option.label}`);
    onSheetChanged?.();
  };

  const onNew = () => {
    form.resetFields();
    setSheet(null);
    setResult(null);
    setLastInput(null);
    setVersionId(null);
    setPriceOverrides({});
    onLocate?.(null);
  };

  const mode = values?.mode ?? 'target_profit';
  const markets = values?.markets ?? [];
  const attrs = values?.attributes ?? [];
  const showLiquid = attrs.includes('liquid') || attrs.includes('paste');
  const needPrice = mode === 'fixed_price' || mode === 'max_purchase';
  const categoryOptions = useMemo(() => {
    const chosen = meta.markets.filter((m) => markets.includes(m.code));
    const all = new Map<string, { name: string; sites: string[] }>();
    for (const m of chosen) {
      for (const c of m.categories) {
        const e = all.get(c.key) ?? { name: c.name, sites: [] };
        e.sites.push(m.code);
        all.set(c.key, e);
      }
    }
    return [...all.entries()].map(([key, e]) => ({
      value: key,
      label: e.sites.length === chosen.length ? e.name : `${e.name}（仅 ${e.sites.join('/')}）`,
    }));
  }, [meta.markets, markets]);

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        {sheet ? (
          <>
            <Typography.Text strong>{sheet.product_name}</Typography.Text>
            <Tag>{sheet.status === 'saved' ? '已保存' : '草稿'}</Tag>
            {versionId !== null ? (
              <Tag color="blue">v{sheet.versions.find((x) => x.id === versionId)?.version_no}</Tag>
            ) : null}
            {sheet.rules_updated ? <Tag color="orange">规则已更新，建议重新核价</Tag> : null}
          </>
        ) : (
          <Typography.Text type="secondary">新核价（未保存）</Typography.Text>
        )}
        <Button onClick={onNew}>新建核价</Button>
      </Space>

      <Form form={form} layout="vertical" initialValues={DEFAULT_FORM_VALUES} requiredMark="optional">
        <Card size="small" title="产品与成本" style={{ marginBottom: 12 }}>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="product_name" label="产品名称" rules={[{ required: true, message: '请填写产品名称' }]}>
                <Input maxLength={200} />
              </Form.Item>
            </Col>
            <Col xs={12} md={4}>
              <Form.Item name="sku" label="SKU">
                <Input maxLength={100} />
              </Form.Item>
            </Col>
            <Col xs={12} md={4}>
              <Form.Item name="asin" label="ASIN">
                <Input maxLength={20} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="source_url" label="来源链接">
                <Input maxLength={2000} />
              </Form.Item>
            </Col>
            {mode !== 'max_purchase' ? (
              <Col xs={12} md={4}>
                <Form.Item name="purchase_cost" label="采购成本（¥/件）" rules={[{ required: true, message: '必填' }]}>
                  {num()}
                </Form.Item>
              </Col>
            ) : null}
            <Col xs={12} md={4}>
              <Form.Item name="packaging_labor" label="包装与人工（¥/件）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={12} md={4}>
              <Form.Item name="weight_kg" label="包装后重量（kg）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="包装后尺寸（cm，长 × 宽 × 高）">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="length_cm" noStyle>
                    {num({ placeholder: '长' })}
                  </Form.Item>
                  <Form.Item name="width_cm" noStyle>
                    {num({ placeholder: '宽' })}
                  </Form.Item>
                  <Form.Item name="height_cm" noStyle>
                    {num({ placeholder: '高' })}
                  </Form.Item>
                </Space.Compact>
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Form.Item name="attributes" label="货物属性">
                <Checkbox.Group options={meta.attributes.map((a) => ({ label: a.name, value: a.key }))} />
              </Form.Item>
            </Col>
            {showLiquid ? (
              <>
                <Col xs={12} md={4}>
                  <Form.Item name="liquid_ml" label="单瓶容量（ml）">
                    {num()}
                  </Form.Item>
                </Col>
                <Col xs={12} md={4}>
                  <Form.Item name="bottles" label="每单瓶数">
                    <InputNumber min={1} precision={0} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </>
            ) : null}
          </Row>
        </Card>

        <Card size="small" title="核价条件" style={{ marginBottom: 12 }}>
          <Row gutter={16}>
            <Col xs={24}>
              <Form.Item name="mode" label="核价方式">
                <Radio.Group optionType="button" options={MODE_OPTIONS} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="markets" label="市场" rules={[{ required: true, message: '至少选择一个市场' }]}>
                <Checkbox.Group
                  options={meta.markets.map((m) => ({
                    value: m.code,
                    disabled: m.missing.length > 0,
                    label: m.missing.length
                      ? `${m.name} ${m.code}（未配置：${m.missing.map((k) => kindName[k] ?? k).join('、')}）`
                      : `${m.name} ${m.code}`,
                  }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="category" label="佣金类目" rules={[{ required: true, message: '请选择类目' }]}>
                <Select options={categoryOptions} placeholder={markets.length ? '选择类目' : '先选市场'} showSearch
                  optionFilterProp="label" />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="发货方式">
                <Space>
                  <Form.Item name="include_fba" valuePropName="checked" noStyle>
                    <Switch checkedChildren="FBA" unCheckedChildren="FBA" />
                  </Form.Item>
                  <Form.Item name="include_fbm" valuePropName="checked" noStyle>
                    <Switch checkedChildren="自发货" unCheckedChildren="自发货" />
                  </Form.Item>
                </Space>
              </Form.Item>
            </Col>
            {mode === 'fixed_price' || mode === 'max_purchase' ? (
              <Col xs={24} md={6}>
                <Form.Item name="target_type" label="目标类型">
                  <Radio.Group
                    options={[
                      { label: '利润额', value: 'profit' },
                      { label: '利润率', value: 'margin' },
                    ]}
                  />
                </Form.Item>
              </Col>
            ) : null}
            {mode === 'target_profit' ||
            ((mode === 'fixed_price' || mode === 'max_purchase') && values?.target_type !== 'margin') ? (
              <Col xs={12} md={6}>
                <Form.Item
                  name="target_profit"
                  label={mode === 'fixed_price' ? '目标单件利润（¥，可选）' : '目标单件利润（¥）'}
                  rules={mode === 'fixed_price' ? [] : [{ required: true, message: '必填' }]}
                >
                  <InputNumber stringMode style={{ width: '100%' }} controls={false} />
                </Form.Item>
              </Col>
            ) : null}
            {mode === 'target_margin' ||
            ((mode === 'fixed_price' || mode === 'max_purchase') && values?.target_type === 'margin') ? (
              <Col xs={12} md={6}>
                <Form.Item
                  name="target_margin_pct"
                  label="目标利润率（%）"
                  rules={mode === 'fixed_price' ? [] : [{ required: true, message: '必填' }]}
                >
                  {num({ max: '99.99' })}
                </Form.Item>
              </Col>
            ) : null}
            <Col xs={24} md={6}>
              <Form.Item name="declared_basis" label="申报价值（关税计税基础）">
                <Radio.Group
                  options={[
                    { label: '按采购成本', value: 'purchase_cost' },
                    { label: '固定金额', value: 'fixed' },
                  ]}
                />
              </Form.Item>
            </Col>
            {values?.declared_basis === 'fixed' ? (
              <Col xs={12} md={4}>
                <Form.Item name="declared_value" label="申报价值（¥）" rules={[{ required: true, message: '必填' }]}>
                  {num()}
                </Form.Item>
              </Col>
            ) : null}
          </Row>
        </Card>

        <Card size="small" title="按市场填写" style={{ marginBottom: 12 }}>
          {markets.length ? (
            <Tabs
              size="small"
              items={markets.map((site) => {
                const m = meta.markets.find((x) => x.code === site);
                const cur = m?.currency ?? '';
                return {
                  key: site,
                  label: `${site} · ${cur}`,
                  children: (
                    <Row gutter={16}>
                      {needPrice ? (
                        <Col xs={12} md={4}>
                          <Form.Item
                            name={['market_inputs', site, 'price']}
                            label={`售价（${cur}${m?.price_includes_vat ? '，含税' : ''}）`}
                            rules={[{ required: true, message: '必填' }]}
                          >
                            {num()}
                          </Form.Item>
                        </Col>
                      ) : null}
                      <Col xs={12} md={4}>
                        <Form.Item
                          name={['market_inputs', site, 'duty_pct']}
                          label="关税税率（%）"
                          tooltip="由商品 HS 编码与原产地决定，系统不提供默认值；免税请明确填 0。FBA 与不包税渠道需要。"
                        >
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'ads_pct']} label="广告预留（%）"
                          tooltip="不填取市场默认值">
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'return_pct']} label="退货损耗（%）"
                          tooltip="不填取市场默认值">
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'payout_pct']} label="结汇手续费（%）"
                          tooltip="不填取市场默认值">
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'storage_months']} label="FBA 平均在仓（月）"
                          tooltip="不填取市场默认值">
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'headhaul_per_unit']} label="FBA 头程（¥/件）"
                          tooltip="不填按市场默认头程报价计算；市场没有默认报价时必填">
                          {num()}
                        </Form.Item>
                      </Col>
                      <Col xs={12} md={4}>
                        <Form.Item name={['market_inputs', site, 'fba_fee_override']} label={`手填 FBA 配送费（${cur}）`}
                          tooltip="超出已配置尺寸段，或以卖家后台收入计算器为准时填写；来源记为「人工填写」">
                          {num()}
                        </Form.Item>
                      </Col>
                    </Row>
                  ),
                };
              })}
            />
          ) : (
            <Typography.Text type="secondary">先在「核价条件」中选择市场</Typography.Text>
          )}
          <Form.List name="competitor_prices">
            {(fields, { add, remove }) => (
              <div>
                <Typography.Text strong>竞品价格对照（可选）</Typography.Text>
                {fields.map((f) => (
                  <Space key={f.key} align="baseline" wrap style={{ display: 'flex', marginTop: 8 }}>
                    <Form.Item name={[f.name, 'market']} noStyle>
                      <Select style={{ width: 90 }} placeholder="市场" options={markets.map((x) => ({ value: x, label: x }))} />
                    </Form.Item>
                    <Form.Item name={[f.name, 'price']} noStyle>
                      {num({ placeholder: '价格', style: { width: 120 } })}
                    </Form.Item>
                    <Form.Item name={[f.name, 'includes_vat']} valuePropName="checked" noStyle>
                      <Checkbox>含税价</Checkbox>
                    </Form.Item>
                    <Form.Item name={[f.name, 'note']} noStyle>
                      <Input placeholder="说明（链接、日期、口径）" style={{ width: 260 }} />
                    </Form.Item>
                    <Button type="link" onClick={() => remove(f.name)}>
                      删除
                    </Button>
                  </Space>
                ))}
                <Button type="dashed" size="small" style={{ marginTop: 8 }} onClick={() => add({ includes_vat: false })}>
                  添加竞品价格
                </Button>
              </div>
            )}
          </Form.List>
        </Card>
      </Form>

      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" loading={busy === 'calc'} onClick={() => runCalc().catch(() => undefined)}>
          开始核价
        </Button>
        <Button loading={busy === 'save'} disabled={!result || stale} onClick={() => onSave().catch(() => undefined)}>
          保存为核价记录
        </Button>
        <Button loading={busy === 'draft'} onClick={() => onSaveDraft().catch(() => undefined)}>
          存草稿
        </Button>
        <Button loading={busy === 'export'} disabled={!fromSavedVersion} onClick={() => onExport().catch(() => undefined)}>
          导出
        </Button>
        {result && !fromSavedVersion && !stale ? (
          <Typography.Text type="secondary">试算结果未保存；保存后才能选定方案和导出。</Typography.Text>
        ) : null}
      </Space>

      {result ? (
        <ResultPanel
          result={result}
          stale={stale}
          savedVersionId={fromSavedVersion ? versionId : null}
          selections={sheet?.selections}
          canSelect={fromSavedVersion}
          priceOverrides={priceOverrides}
          onAdjust={onAdjust}
          onSelect={(market, option) => setSelecting({ market, option })}
        />
      ) : (
        <Card size="small">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="填写条件后点击「开始核价」" />
        </Card>
      )}

      <Modal
        title={selecting ? `选定 ${selecting.market} 方案：${selecting.option.label}` : ''}
        open={!!selecting}
        onCancel={() => setSelecting(null)}
        onOk={() => confirmSelect().catch(() => undefined)}
        okText="选定"
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="每个市场只选定一个方案；换选会留下记录。选定只代表核价结论，不代表已通过风险审核。" />
        <Input.TextArea rows={3} maxLength={500} placeholder="选定说明（可选）" value={selectNote}
          onChange={(e) => setSelectNote(e.target.value)} />
      </Modal>
    </div>
  );
}
