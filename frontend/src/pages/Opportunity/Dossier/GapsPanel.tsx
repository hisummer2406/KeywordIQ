import { PlusOutlined } from '@ant-design/icons';
import { Button, DatePicker, Form, Input, Modal, Popconfirm, Select, Tag, Typography, theme } from 'antd';
import { useState } from 'react';
import type { DossierDetail, ManualCategory, ManualEvidence } from '@/services/opportunityDossier';
import { CATEGORY } from './dossierView';

/**
 * 证据缺口与人工证据。这些类别系统没有数据源，恒显示「未接入」——有人补了资料也不改变这一点，
 * 只是多了带来源、带日期、带署名的人工证据。不显示 0，不估算。
 */

interface Props {
  detail: DossierDetail;
  onAdd: (body: { category: ManualCategory; content: string; source_name: string; source_url?: string | null; observed_date: string }) => Promise<void>;
  onVoid: (evidenceId: number) => Promise<void>;
}

interface Values {
  category: ManualCategory;
  content: string;
  source_name: string;
  source_url?: string;
  /** DatePicker 的值（dayjs 对象）；dayjs 不是直接依赖，这里只用到 format。 */
  observed_date: { format: (fmt: string) => string };
}

function EvidenceItem({ e, onVoid }: { e: ManualEvidence; onVoid: () => Promise<void> }) {
  const { token } = theme.useToken();
  const voided = !!e.voided_at;
  return (
    <div style={{ padding: '6px 0', borderTop: `1px dashed ${token.colorBorderSecondary}`, opacity: voided ? 0.55 : 1 }}>
      <div style={{ whiteSpace: 'pre-wrap', textDecoration: voided ? 'line-through' : undefined }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          #{e.id}{' '}
        </Typography.Text>
        {e.content}
      </div>
      <div style={{ fontSize: 12, color: token.colorTextTertiary, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span>
          来源：
          {e.source_url ? (
            <a href={e.source_url} target="_blank" rel="noreferrer noopener">
              {e.source_name}
            </a>
          ) : (
            e.source_name
          )}
        </span>
        <span>观测 {e.observed_date}</span>
        <span>
          {e.created_by_name ?? '—'} 填于 {e.created_at.slice(0, 10)}
        </span>
        {voided ? (
          <Tag>已作废{e.voided_by_name ? ` · ${e.voided_by_name}` : ''}</Tag>
        ) : (
          <Popconfirm title="作废这条证据？" description="作废后仍保留可见，但不再算作有效证据。" onConfirm={onVoid}>
            <Button type="link" size="small" style={{ padding: 0, height: 'auto', fontSize: 12 }}>
              作废
            </Button>
          </Popconfirm>
        )}
      </div>
    </div>
  );
}

export default function GapsPanel({ detail, onAdd, onVoid }: Props) {
  const { token } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const start = (category: ManualCategory) => {
    form.resetFields();
    form.setFieldsValue({ category });
    setOpen(true);
  };

  const submit = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      await onAdd({
        category: v.category,
        content: v.content.trim(),
        source_name: v.source_name.trim(),
        source_url: v.source_url?.trim() || null,
        observed_date: v.observed_date.format('YYYY-MM-DD'),
      });
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const blocks: { category: ManualCategory; evidence: ManualEvidence[]; gap: boolean }[] = [
    ...detail.gaps.map((g) => ({ category: g.category as ManualCategory, evidence: g.evidence, gap: true })),
    { category: 'other', evidence: detail.other_evidence, gap: false },
  ];

  return (
    <div>
      <Typography.Title level={5} style={{ marginBottom: 4 }}>
        系统没有的数据
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        下面这些数据源都还没接入，所以这里不显示任何数字。你在别处查到的资料可以补在对应类别下，写清来源和观测日期，团队其他人就不用重查。
      </Typography.Paragraph>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
        {blocks.map(({ category, evidence, gap }) => {
          const valid = evidence.filter((e) => !e.voided_at).length;
          return (
            <div key={category} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 10, padding: 12, background: token.colorBgContainer }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Typography.Text strong>{CATEGORY[category].text}</Typography.Text>
                {gap && <Tag>未接入</Tag>}
                {valid > 0 && <Tag color="blue">人工证据 {valid}</Tag>}
                <span style={{ flex: 1 }} />
                <Button type="link" size="small" icon={<PlusOutlined />} onClick={() => start(category)} style={{ padding: 0 }}>
                  补一条
                </Button>
              </div>
              <div style={{ fontSize: 12, color: token.colorTextTertiary, marginBottom: evidence.length ? 6 : 0 }}>{CATEGORY[category].hint}</div>
              {evidence.map((e) => (
                <EvidenceItem key={e.id} e={e} onVoid={() => onVoid(e.id)} />
              ))}
            </div>
          );
        })}
      </div>

      <Modal
        open={open}
        title="补一条人工证据"
        okText="保存"
        confirmLoading={saving}
        onOk={() => submit().catch(() => undefined)}
        onCancel={() => setOpen(false)}
        destroyOnClose
      >
        <Form<Values> form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="category" label="类别" rules={[{ required: true }]}>
            <Select options={(Object.keys(CATEGORY) as ManualCategory[]).map((c) => ({ value: c, label: CATEGORY[c].text }))} />
          </Form.Item>
          <Form.Item name="content" label="内容" rules={[{ required: true, whitespace: true, message: '写下查到的内容' }, { max: 4000 }]}>
            <Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} placeholder="例如：B0HPF892FJ 近 30 天销量估算约 3,200 件，售价 $19.99" />
          </Form.Item>
          <Form.Item name="source_name" label="来源" rules={[{ required: true, whitespace: true, message: '写清来源' }, { max: 100 }]}>
            <Input placeholder="例如：卖家精灵网页、Amazon 商品页、供应商报价单" maxLength={100} />
          </Form.Item>
          <Form.Item
            name="source_url"
            label="来源链接（可选）"
            rules={[{ pattern: /^https?:\/\/\S+$/i, message: '只接受 http 或 https 地址' }, { max: 500 }]}
          >
            <Input placeholder="https://" />
          </Form.Item>
          <Form.Item name="observed_date" label="观测日期" rules={[{ required: true, message: '选择这条数据是哪天看到的' }]}>
            <DatePicker disabledDate={(d) => d.format('YYYY-MM-DD') > detail.today} />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          保存后不能修改，写错了可以作废再补一条。
        </Typography.Text>
      </Modal>
    </div>
  );
}
