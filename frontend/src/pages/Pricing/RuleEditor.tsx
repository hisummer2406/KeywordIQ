import {
  Alert,
  Button,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  createRule,
  pctToRate,
  rateToPct,
  updateRule,
  type PricingMeta,
  type Rule,
  type RuleKind,
} from '@/services/pricing';

/**
 * 费用规则编辑（design D8）。
 *
 * 汇率、市场参数、自发货渠道用结构化表单——这三类最常由运营按报价单维护。类目佣金与
 * FBA 费率结构复杂（尺寸段 × 重量区间 × 价格段），首版用 JSON 编辑，由后端按 kind 严格
 * 校验并给出字段级错误；初始数据由种子脚本按官方费率卡生成草稿，日常只需核对与复制改版。
 *
 * 已启用/停用的规则只读：要改就「复制为新草稿」（启用后不可原地修改）。
 */

interface Props {
  open: boolean;
  meta: PricingMeta;
  rule: Rule | null;
  /** 新建时的类别 */
  kind: RuleKind;
  readOnly: boolean;
  onClose: () => void;
  onSaved: (r: Rule) => void;
}

const num = (props: Record<string, unknown> = {}) => (
  <InputNumber stringMode min="0" style={{ width: '100%' }} controls={false} {...props} />
);

const clean = (v: unknown) => (v === '' || v === undefined ? null : v);

function dropNulls(obj: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== ''));
}

// ---------------------------------------------------------------- 载荷 ↔ 表单

function toForm(kind: RuleKind, payload: Record<string, any> | undefined): Record<string, any> {
  const p = payload ?? {};
  if (kind === 'fx_rate') return { cny_per_unit: p.cny_per_unit };
  if (kind === 'market_params') {
    return {
      price_includes_vat: p.price_includes_vat ?? false,
      vat_pct: rateToPct(p.vat_rate ?? '0'),
      import_vat_recoverable: p.import_vat_recoverable ?? true,
      surcharge_pct: rateToPct(p.amazon_fee_surcharge_rate ?? '0'),
      ads_pct: rateToPct(p.default_ads_rate ?? '0'),
      return_pct: rateToPct(p.default_return_rate ?? '0'),
      payout_pct: rateToPct(p.default_payout_rate ?? '0'),
      storage_months: p.default_storage_months ?? '0',
      headhaul_mode: p.headhaul?.mode ?? 'none',
      headhaul_price: p.headhaul?.unit_price,
      headhaul_divisor: p.headhaul?.volumetric_divisor,
    };
  }
  if (kind === 'fbm_channel') {
    return {
      ...p,
      allowed_attributes: p.allowed_attributes ?? ['general'],
      liquid_cap_scope: p.liquid_cap_scope ?? 'per_bottle',
      min_weight_kg: p.min_weight_kg ?? '0',
      weight_step_kg: p.weight_step_kg ?? '0.001',
      duty_mode: p.duty_mode ?? 'not_included',
      brackets: p.brackets ?? [{}],
      surcharges: p.surcharges ?? [],
    };
  }
  return { json: payload ? JSON.stringify(payload, null, 2) : '' };
}

function fromForm(kind: RuleKind, v: Record<string, any>, scope: string): Record<string, any> {
  if (kind === 'fx_rate') return { currency: scope, cny_per_unit: v.cny_per_unit };
  if (kind === 'market_params') {
    return {
      price_includes_vat: !!v.price_includes_vat,
      vat_rate: v.price_includes_vat ? pctToRate(v.vat_pct) ?? '0' : '0',
      import_vat_recoverable: !!v.import_vat_recoverable,
      amazon_fee_surcharge_rate: pctToRate(v.surcharge_pct) ?? '0',
      default_ads_rate: pctToRate(v.ads_pct) ?? '0',
      default_return_rate: pctToRate(v.return_pct) ?? '0',
      default_payout_rate: pctToRate(v.payout_pct) ?? '0',
      default_storage_months: clean(v.storage_months) ?? '0',
      headhaul:
        v.headhaul_mode && v.headhaul_mode !== 'none'
          ? dropNulls({
              mode: v.headhaul_mode,
              unit_price: clean(v.headhaul_price),
              volumetric_divisor: clean(v.headhaul_divisor),
            })
          : null,
    };
  }
  if (kind === 'fbm_channel') {
    const base = dropNulls({
      site: v.site,
      code: v.code,
      name: v.name,
      carrier: v.carrier ?? '',
      allowed_attributes: v.allowed_attributes,
      max_weight_kg: clean(v.max_weight_kg),
      max_longest_cm: clean(v.max_longest_cm),
      max_dims_sum_cm: clean(v.max_dims_sum_cm),
      liquid_cap_ml: clean(v.liquid_cap_ml),
      liquid_cap_scope: v.liquid_cap_scope,
      min_weight_kg: clean(v.min_weight_kg) ?? '0',
      weight_step_kg: clean(v.weight_step_kg) ?? '0.001',
      volumetric_divisor: clean(v.volumetric_divisor),
      duty_mode: v.duty_mode,
      transit_days: clean(v.transit_days),
    });
    return {
      ...base,
      brackets: (v.brackets ?? []).map((b: any) => ({
        max_weight_kg: b.max_weight_kg,
        per_parcel_fee: b.per_parcel_fee ?? '0',
        per_kg: b.per_kg ?? '0',
      })),
      surcharges: (v.surcharges ?? []).map((s: any) => ({
        name: s.name,
        kind: s.kind,
        value: s.value ?? '0',
        when_attributes: s.when_attributes ?? [],
      })),
      ...(v.source_cells ? { source_cells: v.source_cells } : {}),
    };
  }
  return JSON.parse(v.json || '{}');
}

// ---------------------------------------------------------------- 组件

export default function RuleEditor({ open, meta, rule, kind, readOnly, onClose, onSaved }: Props) {
  const [form] = Form.useForm();
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const k = rule?.kind ?? kind;
  const attrOptions = meta.attributes.map((a) => ({ label: a.name, value: a.key }));
  const siteOptions = meta.markets.map((m) => ({ value: m.code, label: `${m.name} ${m.code}` }));
  const currencyOptions = [...new Set(meta.markets.map((m) => m.currency))].map((c) => ({ value: c, label: c }));

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    setJsonError(null);
    form.setFieldsValue({
      scope_key: rule?.scope_key,
      effective_from: rule?.effective_from ?? meta.today,
      effective_to: rule?.effective_to ?? '',
      source: rule?.source ?? '',
      review_note: rule?.review_note ?? '',
      ...toForm(k, rule?.payload),
    });
  }, [open, rule, k, form, meta.today]);

  const onOk = async () => {
    if (readOnly) return onClose();
    const v = await form.validateFields();
    const scope = k === 'fbm_channel' ? `${v.site}:${v.code}` : v.scope_key;
    let payload: Record<string, any>;
    try {
      payload = fromForm(k, v, scope);
    } catch {
      setJsonError('JSON 格式不正确');
      return;
    }
    setSaving(true);
    try {
      const common = {
        effective_from: v.effective_from,
        source: v.source,
        review_note: v.review_note || null,
        payload,
      };
      const saved = rule
        ? await updateRule(rule.id, {
            lock_version: rule.lock_version,
            ...common,
            ...(v.effective_to ? { effective_to: v.effective_to } : { clear_effective_to: true }),
          })
        : await createRule({ kind: k, scope_key: scope, effective_to: v.effective_to || null, ...common });
      onSaved(saved);
    } finally {
      setSaving(false);
    }
  };

  const kindName = meta.rule_kinds.find((x) => x.key === k)?.name ?? k;
  return (
    <Modal
      width={900}
      open={open}
      title={rule ? `${kindName} · ${rule.scope_key} · v${rule.version}${readOnly ? '（只读）' : ''}` : `新建${kindName}草稿`}
      onCancel={onClose}
      onOk={() => onOk().catch(() => undefined)}
      okText={readOnly ? '关闭' : '保存草稿'}
      confirmLoading={saving}
      destroyOnHidden
    >
      {rule && rule.status !== 'draft' ? (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          title="已启用或停用的规则不能修改；需要改动请在列表中「复制为新草稿」。" />
      ) : null}
      <Form form={form} layout="vertical" disabled={readOnly}>
        <Row gutter={16}>
          {k !== 'fbm_channel' ? (
            <Col xs={12} md={6}>
              <Form.Item name="scope_key" label={k === 'fx_rate' ? '币种' : '市场'} rules={[{ required: true }]}>
                <Select disabled={!!rule || readOnly} options={k === 'fx_rate' ? currencyOptions : siteOptions} />
              </Form.Item>
            </Col>
          ) : null}
          <Col xs={12} md={6}>
            <Form.Item name="effective_from" label="生效日（含）" rules={[{ required: true }]}>
              <Input type="date" />
            </Form.Item>
          </Col>
          <Col xs={12} md={6}>
            <Form.Item name="effective_to" label="失效日（含，可空）">
              <Input type="date" />
            </Form.Item>
          </Col>
          <Col xs={24}>
            <Form.Item name="source" label="来源（必填：官方链接、报价单、原表单元格）" rules={[{ required: true }]}>
              <Input.TextArea rows={2} maxLength={2000} />
            </Form.Item>
          </Col>
          <Col xs={24}>
            <Form.Item name="review_note" label="审核说明（冲突与待核实项）">
              <Input.TextArea rows={2} maxLength={4000} />
            </Form.Item>
          </Col>
        </Row>

        {k === 'fx_rate' ? (
          <Form.Item name="cny_per_unit" label="1 单位外币 = 多少人民币" rules={[{ required: true }]}>
            {num()}
          </Form.Item>
        ) : null}

        {k === 'market_params' ? (
          <Row gutter={16}>
            <Col xs={12} md={6}>
              <Form.Item name="price_includes_vat" label="前台标价含增值税" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="vat_pct" label="增值税率（%）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="import_vat_recoverable" label="进口增值税可抵扣" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="surcharge_pct" label="平台费用附加（%）" tooltip="如英国数字服务税对 Amazon 费用加收 2%">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="ads_pct" label="默认广告预留（%）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="return_pct" label="默认退货损耗（%）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="payout_pct" label="默认结汇手续费（%）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="storage_months" label="默认 FBA 在仓月数">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="headhaul_mode" label="默认头程报价">
                <Select
                  options={[
                    { value: 'none', label: '不设（核价时手填）' },
                    { value: 'per_kg', label: '按计费重（元/kg）' },
                    { value: 'per_cbm', label: '按体积（元/m³）' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="headhaul_price" label="头程单价（元）">
                {num()}
              </Form.Item>
            </Col>
            <Col xs={8} md={6}>
              <Form.Item name="headhaul_divisor" label="头程体积重除数（cm³/kg）">
                {num()}
              </Form.Item>
            </Col>
          </Row>
        ) : null}

        {k === 'fbm_channel' ? (
          <>
            <Row gutter={16}>
              <Col xs={8} md={4}>
                <Form.Item name="site" label="市场" rules={[{ required: true }]}>
                  <Select disabled={!!rule || readOnly} options={siteOptions} />
                </Form.Item>
              </Col>
              <Col xs={8} md={5}>
                <Form.Item name="code" label="渠道码" rules={[{ required: true }]}>
                  <Input disabled={!!rule || readOnly} maxLength={40} />
                </Form.Item>
              </Col>
              <Col xs={8} md={5}>
                <Form.Item name="carrier" label="承运商">
                  <Input maxLength={60} />
                </Form.Item>
              </Col>
              <Col xs={24} md={10}>
                <Form.Item name="name" label="渠道名称" rules={[{ required: true }]}>
                  <Input maxLength={100} />
                </Form.Item>
              </Col>
              <Col xs={24}>
                <Form.Item name="allowed_attributes" label="允许的货物属性" rules={[{ required: true }]}>
                  <Checkbox.Group options={attrOptions} />
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="max_weight_kg" label="重量上限（kg）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="max_longest_cm" label="最长边上限（cm）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="max_dims_sum_cm" label="三边和上限（cm）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="liquid_cap_ml" label="液体/膏体上限（ml）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="liquid_cap_scope" label="容量计量对象">
                  <Select options={[{ value: 'per_bottle', label: '单瓶' }, { value: 'per_parcel', label: '整单' }]} />
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="duty_mode" label="关税">
                  <Select options={[{ value: 'included', label: '包税' }, { value: 'not_included', label: '不包税' }]} />
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="min_weight_kg" label="起重（kg）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="weight_step_kg" label="进位粒度（kg）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="volumetric_divisor" label="体积重除数（空=按实重）">
                  {num()}
                </Form.Item>
              </Col>
              <Col xs={8} md={4}>
                <Form.Item name="transit_days" label="时效（展示用）">
                  <Input maxLength={40} />
                </Form.Item>
              </Col>
            </Row>
            <Typography.Text strong>重量区间（按计费重命中第一个上限 ≥ 计费重的区间）</Typography.Text>
            <Form.List name="brackets">
              {(fields, { add, remove }) => (
                <div style={{ marginBottom: 12 }}>
                  {fields.map((f) => (
                    <Space key={f.key} align="baseline" style={{ display: 'flex', marginTop: 8 }}>
                      <Form.Item name={[f.name, 'max_weight_kg']} rules={[{ required: true, message: '上限必填' }]} noStyle>
                        {num({ placeholder: '计费重上限 kg', style: { width: 150 } })}
                      </Form.Item>
                      <Form.Item name={[f.name, 'per_parcel_fee']} noStyle>
                        {num({ placeholder: '挂号费 元/票', style: { width: 150 } })}
                      </Form.Item>
                      <Form.Item name={[f.name, 'per_kg']} noStyle>
                        {num({ placeholder: '单价 元/kg', style: { width: 150 } })}
                      </Form.Item>
                      {!readOnly ? <Button type="link" onClick={() => remove(f.name)}>删除</Button> : null}
                    </Space>
                  ))}
                  {!readOnly ? (
                    <Button type="dashed" size="small" style={{ marginTop: 8 }} onClick={() => add({})}>
                      添加重量区间
                    </Button>
                  ) : null}
                </div>
              )}
            </Form.List>
            <Typography.Text strong>附加费</Typography.Text>
            <Form.List name="surcharges">
              {(fields, { add, remove }) => (
                <div>
                  {fields.map((f) => (
                    <Space key={f.key} align="baseline" wrap style={{ display: 'flex', marginTop: 8 }}>
                      <Form.Item name={[f.name, 'name']} rules={[{ required: true, message: '名称必填' }]} noStyle>
                        <Input placeholder="名称" style={{ width: 220 }} />
                      </Form.Item>
                      <Form.Item name={[f.name, 'kind']} initialValue="fixed" noStyle>
                        <Select
                          style={{ width: 150 }}
                          options={[
                            { value: 'fixed', label: '元/票' },
                            { value: 'per_kg', label: '元/计费 kg' },
                            { value: 'rate_on_freight', label: '运费比例' },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item name={[f.name, 'value']} noStyle>
                        {num({ placeholder: '数值', style: { width: 110 } })}
                      </Form.Item>
                      <Form.Item name={[f.name, 'when_attributes']} noStyle>
                        <Select mode="multiple" placeholder="仅这些属性收取（空=都收）" style={{ minWidth: 220 }}
                          options={attrOptions} />
                      </Form.Item>
                      {!readOnly ? <Button type="link" onClick={() => remove(f.name)}>删除</Button> : null}
                    </Space>
                  ))}
                  {!readOnly ? (
                    <Button type="dashed" size="small" style={{ marginTop: 8 }} onClick={() => add({ kind: 'fixed' })}>
                      添加附加费
                    </Button>
                  ) : null}
                </div>
              )}
            </Form.List>
          </>
        ) : null}

        {k === 'referral_schedule' || k === 'fba_rate_card' ? (
          <>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              {k === 'fba_rate_card'
                ? '结构：unit_system、dim_divisor、tiers（尺寸段）、fees（尺寸段 × 价格段 × 重量区间，可带 overflow 续重）、fuel_surcharge_rate、storage_rates、placement_fees。金额一律写成字符串。'
                : '结构：categories[]，每个类目含 key、name、mode（whole 整价 / portion 分段）、tiers[{up_to, rate}]（最后一档不设 up_to）、min_fee。金额与比例一律写成字符串。'}
              保存时后端逐字段校验，错误会指出路径。
            </Typography.Paragraph>
            <Form.Item
              name="json"
              rules={[
                { required: true, message: '载荷必填' },
                {
                  validator: async (_, val) => {
                    try {
                      JSON.parse(val || '');
                    } catch {
                      throw new Error('JSON 格式不正确');
                    }
                  },
                },
              ]}
            >
              <Input.TextArea rows={18} style={{ fontFamily: 'monospace', fontSize: 12 }} />
            </Form.Item>
            {jsonError ? <Alert type="error" title={jsonError} /> : null}
          </>
        ) : null}
      </Form>
    </Modal>
  );
}
