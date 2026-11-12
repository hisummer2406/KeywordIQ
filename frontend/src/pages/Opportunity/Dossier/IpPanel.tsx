import { LinkOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Modal, Popconfirm, Select, Tag, Typography, theme } from 'antd';
import { useState } from 'react';
import { DECISION, RISK_LEVEL, TASK_STATUS } from '@/pages/IP/riskLabels';
import { type DossierDetail, listLinkableResearches } from '@/services/opportunityDossier';
import type { Decision as RiskDecision, RiskLevel, TaskStatus } from '@/services/risk';
import { TASK_KIND } from '../IpAgent/ipLabels';

/**
 * 专利风险：关联的 IP 调研摘要。团队里谁都能看到摘要（产品名、创建人、各市场最新人工意见）；
 * 检索式、候选、附图等明细仍只有调研的创建人能打开。没有人工意见时是「未核查」，不是低风险。
 */

interface Props {
  detail: DossierDetail;
  currentUserId: number | null;
  onStartResearch: () => void;
  onOpenResearch: (researchId: number) => void;
  onLink: (researchId: number) => Promise<void>;
  onUnlink: (researchId: number) => Promise<void>;
}

export default function IpPanel({ detail, currentUserId, onStartResearch, onOpenResearch, onLink, onUnlink }: Props) {
  const { token } = theme.useToken();
  const [picking, setPicking] = useState(false);
  const [options, setOptions] = useState<{ value: number; label: string }[]>([]);
  const [chosen, setChosen] = useState<number | undefined>();
  const [saving, setSaving] = useState(false);
  const { ip } = detail;

  const openPicker = async () => {
    setChosen(undefined);
    setPicking(true);
    const r = await listLinkableResearches();
    setOptions(
      r.items
        .filter((x) => x.dossier_id !== detail.dossier.id)
        .map((x) => ({ value: x.id, label: `${x.product_name}${x.dossier_id ? '（已挂在别的档案，选择后改挂到这里）' : ''}` })),
    );
  };

  const link = async () => {
    if (!chosen) return;
    setSaving(true);
    try {
      await onLink(chosen);
      setPicking(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          外观专利风险
        </Typography.Title>
        {ip.state === 'unchecked' ? <Tag color="warning">未核查</Tag> : <Tag color="blue">已有人工意见</Tag>}
        <span style={{ flex: 1 }} />
        <Button size="small" icon={<LinkOutlined />} onClick={() => openPicker().catch(() => undefined)}>
          关联我的调研
        </Button>
        <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={onStartResearch}>
          发起 IP 调研
        </Button>
      </div>
      {ip.researches.length === 0 ? (
        <Typography.Text type="secondary">
          还没有关联专利调研。「未核查」不代表没有风险；准备推进前，建议让 IP 专家按产品外观检索一次外观专利。
        </Typography.Text>
      ) : (
        <div style={{ display: 'grid', gap: 8 }}>
          {ip.researches.map((r) => {
            const mine = r.owner_id === currentUserId;
            return (
              <div key={r.id} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 10, padding: 12, background: token.colorBgContainer }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Typography.Text strong>{r.product_name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {r.owner_name ?? '—'} 创建 · 候选专利 {r.candidate_count} 件
                    {r.last_task &&
                      ` · ${TASK_KIND[r.last_task.kind as keyof typeof TASK_KIND] ?? r.last_task.kind} ${TASK_STATUS[r.last_task.status as TaskStatus]?.text ?? r.last_task.status}`}
                  </Typography.Text>
                  <span style={{ flex: 1 }} />
                  {mine ? (
                    <>
                      <Button type="link" size="small" onClick={() => onOpenResearch(r.id)}>
                        打开调研
                      </Button>
                      <Popconfirm title="取消关联？" description="只是不再挂在这个档案下，调研本身不受影响。" onConfirm={() => onUnlink(r.id)}>
                        <Button type="link" size="small" danger>
                          取消关联
                        </Button>
                      </Popconfirm>
                    </>
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      检索明细仅创建人可见
                    </Typography.Text>
                  )}
                </div>
                <div style={{ marginTop: 6 }}>
                  {r.reviews.length === 0 ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      销售市场 {r.markets.join('、')}：还没有签署人工意见（未核查）
                    </Typography.Text>
                  ) : (
                    r.reviews.map((m) => (
                      <span key={m.market} style={{ marginRight: 14, fontSize: 13 }}>
                        {m.market}：风险 <Tag color={RISK_LEVEL[m.risk_level as RiskLevel]?.color}>{RISK_LEVEL[m.risk_level as RiskLevel]?.text ?? m.risk_level}</Tag>
                        <Tag color={DECISION[m.decision as RiskDecision]?.color}>{DECISION[m.decision as RiskDecision]?.text ?? m.decision}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {m.signer_name ?? ''} {m.created_at?.slice(0, 10)}
                        </Typography.Text>
                      </span>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Modal
        open={picking}
        title="关联我的专利调研"
        okText="关联"
        okButtonProps={{ disabled: !chosen, loading: saving }}
        onOk={() => link().catch(() => undefined)}
        onCancel={() => setPicking(false)}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          只能关联你自己创建的调研。关联后，团队成员能在这个档案里看到它的产品名和各市场的人工意见；检索式、候选和附图仍只有你能看。
        </Typography.Paragraph>
        <Select
          style={{ width: '100%' }}
          showSearch
          optionFilterProp="label"
          placeholder={options.length ? '选择调研' : '你名下没有可关联的调研'}
          options={options}
          value={chosen}
          onChange={setChosen}
        />
      </Modal>
    </div>
  );
}
