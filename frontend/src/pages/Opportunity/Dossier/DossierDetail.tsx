import { ArrowLeftOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Divider, Form, Input, Modal, Result, Space, Spin, Tag, Tooltip, Typography, message, theme } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  addDossierDecision,
  addDossierKeywords,
  addManualEvidence,
  askDossier,
  type DossierDetail as Detail,
  getDossier,
  linkDossierResearch,
  refreshDossierSnapshot,
  removeDossierKeyword,
  unlinkDossierResearch,
  updateDossier,
  voidManualEvidence,
} from '@/services/opportunityDossier';
import AskPanel from './AskPanel';
import DecisionPanel from './DecisionPanel';
import { describeChange, isDue, STATUS } from './dossierView';
import GapsPanel from './GapsPanel';
import IpPanel from './IpPanel';
import KeywordEvidencePanel from './KeywordEvidencePanel';

/**
 * 机会档案详情（change add-opportunity-dossier 决策 9）。自上而下：标题区 → 自上次决定以来的变化 →
 * 关键词与 ABA 证据 → 系统没有的数据 → 专利风险 → 决定 → 追问。
 *
 * 每个写操作成功后重新取一次详情；有在途的追问时每 3 秒轮询，答完就停。
 */

interface Props {
  id: number;
  currentUserId: number | null;
  onBack: () => void;
  /** 列表里展示的字段变了（标题、状态、负责人…） */
  onChanged: () => void;
  onStartResearch: (detail: Detail) => void;
  onOpenResearch: (researchId: number) => void;
}

const POLL_MS = 3000;

export default function DossierDetail({ id, currentUserId, onBack, onChanged, onStartResearch, onOpenResearch }: Props) {
  const { token } = theme.useToken();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [missing, setMissing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form] = Form.useForm<{ title: string; idea?: string }>();
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const d = await getDossier(id);
      if (alive.current) setDetail(d);
    } catch {
      if (alive.current) setMissing(true);
    }
  }, [id]);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const asking = !!detail?.asking;
  useEffect(() => {
    if (!asking) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [asking, load]);

  /** 执行一次写操作，然后刷新详情；`list` 为真时同时通知列表。 */
  const act = async (fn: () => Promise<unknown>, list = false) => {
    await fn();
    await load();
    if (list) onChanged();
  };

  if (missing) return <Result status="404" title="档案不存在" extra={<Button onClick={onBack}>返回档案列表</Button>} />;
  if (!detail) return <Spin style={{ display: 'block', margin: '120px auto' }} />;

  const d = detail.dossier;
  const due = isDue(d.recheck_date, detail.today);
  const stale = detail.snapshot_stale;
  const changes = detail.changes;

  const refresh = async () => {
    setRefreshing(true);
    try {
      const r = await refreshDossierSnapshot(id);
      if (!r.created) message.info('没有新导入的数据，证据仍是最新的');
      await load();
      if (r.created) onChanged();
    } finally {
      setRefreshing(false);
    }
  };

  const saveInfo = async () => {
    const v = await form.validateFields();
    await act(() => updateDossier(id, { title: v.title.trim(), idea: v.idea?.trim() ?? '' }), true);
    setEditing(false);
  };

  return (
    <div style={{ height: '100%', overflowY: 'auto' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '16px 24px 48px' }}>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ padding: 0, marginBottom: 4 }}>
          档案列表
        </Button>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 280 }}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {d.title}
              <Tooltip title="改标题与想法">
                <Button
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  style={{ marginLeft: 6 }}
                  onClick={() => {
                    form.setFieldsValue({ title: d.title, idea: d.idea ?? '' });
                    setEditing(true);
                  }}
                />
              </Tooltip>
            </Typography.Title>
            <div style={{ marginTop: 6, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <Tag color={STATUS[d.status].color}>{STATUS[d.status].text}</Tag>
              <Tag>
                {d.site_name}（{d.site}）
              </Tag>
              {d.archived_at && <Tag>已归档</Tag>}
              {d.recheck_date && <Tag color={due ? 'error' : 'default'}>{due ? `复查已到期 ${d.recheck_date}` : `复查 ${d.recheck_date}`}</Tag>}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                负责人 {d.assignee_name ?? '—'} · {d.creator_name ?? '—'} 建于 {d.created_at.slice(0, 10)}
              </Typography.Text>
            </div>
            {d.idea && (
              <Typography.Paragraph style={{ marginTop: 8, marginBottom: 0, whiteSpace: 'pre-wrap', color: token.colorTextSecondary }}>
                {d.idea}
              </Typography.Paragraph>
            )}
          </div>
          <Space wrap>
            {d.assignee_id !== currentUserId && (
              <Button onClick={() => act(() => updateDossier(id, { take_over: true }), true).catch(() => undefined)}>我来接手</Button>
            )}
            <Button onClick={() => act(() => updateDossier(id, { archived: !d.archived_at }), true).catch(() => undefined)}>
              {d.archived_at ? '取消归档' : '归档'}
            </Button>
            <Button type="primary" icon={<ReloadOutlined />} loading={refreshing} onClick={() => refresh().catch(() => undefined)}>
              刷新证据
            </Button>
          </Space>
        </div>

        {stale && (stale.new_data || stale.keywords_changed) && (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message={
              stale.new_data
                ? `有新导入的报表（日榜最新期 ${detail.latest_anchor_day ?? '—'}），当前证据还停在 ${detail.snapshot?.anchor_day ?? '—'}。点「刷新证据」更新。`
                : '关键词有变动，点「刷新证据」更新。'
            }
          />
        )}
        {due && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="复查日期到了：刷新证据，看看和上次决定时相比有什么变化，然后签一条新的决定。"
          />
        )}

        {changes && (
          <div style={{ marginTop: 16, padding: 14, borderRadius: 10, border: `1px solid ${token.colorPrimaryBorder}`, background: token.colorPrimaryBg }}>
            <Typography.Text strong>{changes.basis === 'decision' ? '自上次决定以来的变化' : '相对上一份证据的变化'}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              对比基线：{changes.base_created_at.slice(0, 10)} 的证据（日榜期次 {changes.base_anchor_day ?? '—'}）→ 现在（{changes.anchor_day ?? '—'}）
            </Typography.Text>
            {changes.periods_unchanged && (
              <div style={{ marginTop: 6, color: token.colorTextSecondary }}>数据期次未变：这段时间没有新导入的报表，排名不会有变化。</div>
            )}
            <div style={{ marginTop: 6 }}>
              {changes.keywords.map((row) => (
                <div key={`${row.state}-${row.keyword}`} style={{ display: 'flex', gap: 10, padding: '2px 0' }}>
                  <span style={{ width: 260, flex: 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.keyword}>
                    {row.keyword}
                  </span>
                  <span style={{ color: token.colorTextSecondary }}>{describeChange(row)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <Divider />
        <KeywordEvidencePanel
          detail={detail}
          onAdd={(keywords) =>
            act(async () => {
              const r = await addDossierKeywords(id, keywords);
              if (r.added < keywords.length) message.info(`有 ${keywords.length - r.added} 个词已在档案里，未重复添加`);
              if (r.unresolved.length) message.info(`${r.unresolved.length} 个词在保留期内没进过榜单，已保留并标明`);
            }, true)
          }
          onRemove={(rowId) => act(() => removeDossierKeyword(id, rowId), true)}
        />
        {detail.snapshot && (
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
            证据快照 #{detail.snapshot.id}，{detail.snapshot.created_by_name ?? '—'} 生成于 {detail.snapshot.created_at.slice(0, 16).replace('T', ' ')}；这份档案共 {detail.snapshot_count} 份快照，旧快照不会被改写。
          </Typography.Paragraph>
        )}

        <Divider />
        <GapsPanel
          detail={detail}
          onAdd={(body) => act(() => addManualEvidence(id, body))}
          onVoid={(evidenceId) => act(() => voidManualEvidence(id, evidenceId))}
        />

        <Divider />
        <IpPanel
          detail={detail}
          currentUserId={currentUserId}
          onStartResearch={() => onStartResearch(detail)}
          onOpenResearch={onOpenResearch}
          onLink={(rid) => act(() => linkDossierResearch(id, rid))}
          onUnlink={(rid) => act(() => unlinkDossierResearch(id, rid))}
        />

        <Divider />
        <DecisionPanel detail={detail} onSign={(body) => act(() => addDossierDecision(id, body), true)} />

        <Divider />
        <AskPanel detail={detail} onAsk={(q) => act(() => askDossier(id, q))} />
      </div>

      <Modal open={editing} title="改标题与想法" okText="保存" onOk={() => saveInfo().catch(() => undefined)} onCancel={() => setEditing(false)} destroyOnClose>
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="title" label="标题" rules={[{ required: true, whitespace: true, message: '写一个标题' }, { max: 100 }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="idea" label="产品想法" rules={[{ max: 4000 }]}>
            <Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
