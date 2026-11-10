import { Alert, Button, Card, DatePicker, Empty, Form, Input, Radio, Select, Space, Tag, Timeline, Typography } from 'antd';
import { useRef, useState } from 'react';
import { addReview, type Candidate, type Decision, newToken, type Review, type RiskLevel } from '@/services/risk';
import { DECISION, DECISIONS_NEED_SEARCH, dt, RISK_LEVEL } from '../riskLabels';

/**
 * 某个销售市场的人工意见：当前意见 + 历史（只追加，不能改、不能删）。
 *
 * 规则与后端 add_review 一致：条件通过须填整改条件、证据和复核日期；检索未完成不能选
 * 可上架或条件通过；系统不预选任何判断——无命中不等于低风险。
 */

interface Props {
  checkId: number;
  market: string;
  marketName: string;
  reviews: Review[];
  candidates: Candidate[];
  searchCompleted: boolean;
  canReview: boolean;
  onSaved: () => void;
}

interface Values {
  risk_level: RiskLevel;
  decision: Decision;
  basis: string;
  uncovered_scope: string;
  conditions?: string;
  evidence_candidate_ids?: number[];
  /** DatePicker 的值（dayjs 对象）；dayjs 不是直接依赖，这里只用到 format。 */
  review_date?: { format: (fmt: string) => string };
}

function ReviewItem({ r, candidates }: { r: Review; candidates: Candidate[] }) {
  const refs = r.evidence_candidate_ids
    .map((id) => candidates.find((c) => c.id === id))
    .map((c, i) => c?.patent_pn ?? c?.patent_id ?? `#${r.evidence_candidate_ids[i]}`);
  return (
    <Space direction="vertical" size={2}>
      <Space wrap>
        <Tag color={DECISION[r.decision].color}>{DECISION[r.decision].text}</Tag>
        <Tag color={RISK_LEVEL[r.risk_level].color}>{`风险：${RISK_LEVEL[r.risk_level].text}`}</Tag>
        <Typography.Text type="secondary">{`${r.signer_name ?? r.signer_id} · ${dt(r.created_at)}`}</Typography.Text>
      </Space>
      <Typography.Text>{`依据：${r.basis}`}</Typography.Text>
      <Typography.Text>{`未覆盖范围：${r.uncovered_scope}`}</Typography.Text>
      {r.conditions && <Typography.Text>{`整改条件：${r.conditions}`}</Typography.Text>}
      {refs.length > 0 && <Typography.Text>{`引用证据：${refs.join('、')}`}</Typography.Text>}
      {r.review_date && <Typography.Text>{`复核日期：${r.review_date}`}</Typography.Text>}
    </Space>
  );
}

export default function ReviewPanel({ checkId, market, marketName, reviews, candidates, searchCompleted, canReview, onSaved }: Props) {
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const token = useRef(newToken());
  const decision = Form.useWatch('decision', form);
  const [current, ...history] = reviews;

  const onFinish = async (v: Values) => {
    setSaving(true);
    try {
      await addReview(checkId, {
        client_token: token.current,
        market,
        risk_level: v.risk_level,
        decision: v.decision,
        basis: v.basis,
        uncovered_scope: v.uncovered_scope,
        conditions: v.conditions || null,
        evidence_candidate_ids: v.evidence_candidate_ids ?? [],
        review_date: v.review_date ? v.review_date.format('YYYY-MM-DD') : null,
      });
      token.current = newToken();
      form.resetFields();
      setEditing(false);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const conditional = decision === 'conditional_pass';

  return (
    <Card
      size="small"
      title={`${marketName}（${market}）`}
      extra={
        current ? (
          <Tag color={DECISION[current.decision].color}>{`当前：${DECISION[current.decision].text}`}</Tag>
        ) : (
          <Tag>未审核</Tag>
        )
      }
    >
      {current ? <ReviewItem r={current} candidates={candidates} /> : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该市场还没有人工意见" />
      )}
      {history.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>{`历史意见（${history.length}）`}</summary>
          <Timeline
            style={{ marginTop: 12 }}
            items={history.map((r) => ({ key: r.id, children: <ReviewItem r={r} candidates={candidates} /> }))}
          />
        </details>
      )}
      {canReview && !editing && (
        <Button style={{ marginTop: 12 }} onClick={() => setEditing(true)}>
          {current ? '追加新意见' : '填写意见'}
        </Button>
      )}
      {canReview && editing && (
        <Form<Values>
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={onFinish}
          onValuesChange={() => {
            token.current = newToken();
          }}
        >
          {!searchCompleted && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="检索尚未完成（排队、失败或结果未知），只能选择继续验证、需补证或不建议上架。"
            />
          )}
          <Form.Item name="risk_level" label="风险判断" rules={[{ required: true, message: '请选择风险判断' }]}>
            <Radio.Group
              options={(Object.keys(RISK_LEVEL) as RiskLevel[]).map((k) => ({ label: RISK_LEVEL[k].text, value: k }))}
            />
          </Form.Item>
          <Form.Item name="decision" label="上架建议" rules={[{ required: true, message: '请选择上架建议' }]}>
            <Select
              placeholder="请选择"
              options={(Object.keys(DECISION) as Decision[]).map((k) => ({
                label: DECISION[k].text,
                value: k,
                disabled: !searchCompleted && DECISIONS_NEED_SEARCH.includes(k),
              }))}
            />
          </Form.Item>
          <Form.Item name="basis" label="依据" rules={[{ required: true, whitespace: true, max: 5000 }]}>
            <Input.TextArea rows={3} placeholder="例如：候选 USD123 外形近似度高，权利有效" />
          </Form.Item>
          <Form.Item
            name="uncovered_scope"
            label="未覆盖范围"
            rules={[{ required: true, whitespace: true, max: 5000 }]}
            extra="写明本次没有检索的法域、视角或未读取的结果，没有就写「无」"
          >
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="conditions"
            label="整改条件"
            rules={[{ required: conditional, whitespace: true, message: '条件通过须填写整改条件' }, { max: 5000 }]}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="evidence_candidate_ids"
            label="引用证据"
            rules={[{ required: conditional, type: 'array', min: conditional ? 1 : 0, message: '条件通过须引用至少一条证据' }]}
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择本次核查中的候选专利"
              optionFilterProp="label"
              options={candidates.map((c) => ({ value: c.id, label: `#${c.rank} ${c.patent_pn ?? c.patent_id}` }))}
            />
          </Form.Item>
          <Form.Item name="review_date" label="复核日期" rules={[{ required: conditional, message: '条件通过须填写复核日期' }]}>
            <DatePicker />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存（签署后不可修改）
            </Button>
            <Button onClick={() => setEditing(false)}>取消</Button>
          </Space>
        </Form>
      )}
    </Card>
  );
}
