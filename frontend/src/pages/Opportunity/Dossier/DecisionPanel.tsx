import { Alert, Button, DatePicker, Form, Input, Radio, Tag, Timeline, Typography, theme } from 'antd';
import { useRef, useState } from 'react';
import type { DossierDetail, Verdict } from '@/services/opportunityDossier';
import { newToken } from '@/services/risk';
import { RECHECK_REQUIRED, STATUS, VERDICTS } from './dossierView';

/**
 * 决定：推进 / 补证 / 观察 / 否决。只追加，每一条都留着签署人、理由、当时依据的证据快照和专利状态。
 * 规则在后端（补证与观察必须定复查日期、没有任何证据不能推进）；这里的校验只是提前提示。
 */

interface Props {
  detail: DossierDetail;
  onSign: (body: { verdict: Verdict; reason: string; next_step?: string | null; recheck_date?: string | null; client_token: string }) => Promise<void>;
}

interface Values {
  verdict: Verdict;
  reason: string;
  next_step?: string;
  /** DatePicker 的值（dayjs 对象）；dayjs 不是直接依赖，这里只用到 format。 */
  recheck_date?: { format: (fmt: string) => string };
}

export default function DecisionPanel({ detail, onSign }: Props) {
  const { token: t } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  const token = useRef(newToken());
  const verdict = Form.useWatch('verdict', form);
  const needRecheck = !!verdict && RECHECK_REQUIRED.includes(verdict);
  const payload = detail.snapshot?.payload;
  // 日榜或周榜任一有在榜记录都算 ABA 证据（与后端 add_decision 一致）
  const hasAba = !!payload?.keywords.some((k) => k.resolved && ((k.day?.ranked_periods ?? 0) > 0 || (k.week?.ranked_periods ?? 0) > 0));
  const hasManual = [...detail.gaps.flatMap((g) => g.evidence), ...detail.other_evidence].some((e) => !e.voided_at);
  const noEvidence = !hasAba && !hasManual;

  const submit = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      await onSign({
        verdict: v.verdict,
        reason: v.reason.trim(),
        next_step: v.next_step?.trim() || null,
        recheck_date: v.recheck_date ? v.recheck_date.format('YYYY-MM-DD') : null,
        client_token: token.current,
      });
      token.current = newToken();
      form.resetFields();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <Typography.Title level={5} style={{ marginBottom: 8 }}>
        决定
      </Typography.Title>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1fr) minmax(320px, 1fr)', gap: 16, alignItems: 'start' }}>
        <div style={{ border: `1px solid ${t.colorBorderSecondary}`, borderRadius: 10, padding: 14, background: t.colorBgContainer }}>
          <Form<Values>
            form={form}
            layout="vertical"
            requiredMark={false}
            onValuesChange={() => {
              token.current = newToken();
            }}
          >
            <Form.Item name="verdict" label="这次的决定" rules={[{ required: true, message: '选择一个决定' }]}>
              <Radio.Group
                optionType="button"
                buttonStyle="solid"
                options={VERDICTS.map((v) => ({ value: v.value, label: v.label, disabled: v.value === 'advance' && noEvidence }))}
              />
            </Form.Item>
            {verdict && (
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -12 }}>
                {VERDICTS.find((v) => v.value === verdict)?.hint}
              </Typography.Paragraph>
            )}
            {noEvidence && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message="档案里还没有任何证据（关键词都没有在榜记录，也没有人工证据），暂时不能签「推进」。"
              />
            )}
            {verdict === 'advance' && detail.ip.state === 'unchecked' && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message="外观专利还没核查。可以照常签署，签署记录会写明当时「专利未核查」。"
              />
            )}
            <Form.Item name="reason" label="理由" rules={[{ required: true, whitespace: true, message: '写下理由' }, { max: 4000 }]}>
              <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} placeholder="依据哪些证据、还担心什么" />
            </Form.Item>
            <Form.Item name="next_step" label="下一步（可选）" rules={[{ max: 2000 }]}>
              <Input.TextArea autoSize={{ minRows: 1, maxRows: 4 }} placeholder="例如：找两家供应商询价；让 IP 专家查外观专利" />
            </Form.Item>
            <Form.Item
              name="recheck_date"
              label={needRecheck ? '复查日期' : '复查日期（可选）'}
              rules={[{ required: needRecheck, message: '补证与观察必须定一个复查日期' }]}
            >
              <DatePicker disabledDate={(d) => d.format('YYYY-MM-DD') < detail.today} />
            </Form.Item>
          </Form>
          <Button type="primary" loading={saving} onClick={() => submit().catch(() => undefined)}>
            签署
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 12 }}>
            签署后不能修改；想法变了就再签一条。
          </Typography.Text>
        </div>

        <div>
          {detail.decisions.length === 0 ? (
            <Typography.Text type="secondary">还没有决定。档案状态是「研究中」。</Typography.Text>
          ) : (
            <Timeline
              items={detail.decisions.map((d) => ({
                color: d.verdict === 'reject' ? 'red' : d.verdict === 'advance' ? 'green' : 'blue',
                children: (
                  <div>
                    <div>
                      <Tag color={STATUS[d.verdict].color}>{STATUS[d.verdict].text}</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {d.signer_name ?? '—'} · {d.created_at.slice(0, 16).replace('T', ' ')}
                        {d.recheck_date && ` · 复查 ${d.recheck_date}`}
                      </Typography.Text>
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{d.reason}</div>
                    {d.next_step && (
                      <div style={{ whiteSpace: 'pre-wrap', color: t.colorTextSecondary }}>
                        下一步：{d.next_step}
                      </div>
                    )}
                    <div style={{ fontSize: 12, color: t.colorTextTertiary, marginTop: 2 }}>
                      签署时：{d.snapshot_id ? `依据 ABA 证据快照 #${d.snapshot_id}` : '没有 ABA 证据快照'}；专利
                      {d.ip_status.state === 'unchecked' ? '未核查' : `已有 ${d.ip_status.reviews.length} 条市场意见`}
                    </div>
                  </div>
                ),
              }))}
            />
          )}
        </div>
      </div>
    </div>
  );
}
