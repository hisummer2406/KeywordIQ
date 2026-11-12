import { SendOutlined } from '@ant-design/icons';
import { Alert, Button, Input, Spin, Tag, Typography, theme } from 'antd';
import { useState } from 'react';
import type { DossierDetail } from '@/services/opportunityDossier';
import { ASK_ERROR } from './dossierView';

/**
 * 追问。大模型只拿到这份档案里的证据（ABA 快照、有效的人工证据、决定、专利摘要），
 * 档案里没有的数据它应当直说没有。回答下方标出引用了哪些证据，便于回到上面核对。
 */

interface Props {
  detail: DossierDetail;
  onAsk: (question: string) => Promise<void>;
}

const SUGGESTIONS = ['这几个词的头部商品稳定吗？', '现有证据支持什么、还缺什么？', '如果要推进，下一步最该先核实什么？'];

export default function AskPanel({ detail, onAsk }: Props) {
  const { token } = theme.useToken();
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const snapshotId = detail.snapshot?.id;

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || detail.asking || sending) return;
    setSending(true);
    try {
      await onAsk(question);
      setText('');
    } finally {
      setSending(false);
    }
  };

  // 证据只有两类：人工证据（列在缺口与「其他」里，含已作废的）与 ABA 快照。按人工证据 id 判断类型，
  // 不能用「是不是最新快照」来猜——刷新证据后，旧回答引用的旧快照会被错标成人工证据（PR #187 复审）。
  const manualIds = new Set([...detail.gaps.flatMap((g) => g.evidence), ...detail.other_evidence].map((e) => e.id));
  const citeLabel = (id: number) =>
    manualIds.has(id) ? `人工证据 #${id}` : id === snapshotId ? 'ABA 证据快照（当前）' : `ABA 证据快照 #${id}（较早）`;

  return (
    <div>
      <Typography.Title level={5} style={{ marginBottom: 4 }}>
        追问
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        回答只基于这份档案里的证据，不查外部数据，也不替你做决定。它是大模型生成的，引用的证据请回到上面核对。
      </Typography.Paragraph>
      {!detail.llm_configured && (
        <Alert type="info" showIcon style={{ marginBottom: 10 }} message="大模型未配置，暂时不能追问。证据、补证与决定都不受影响。" />
      )}
      <div style={{ display: 'grid', gap: 10 }}>
        {detail.messages.map((m) => (
          <div key={m.id} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 10, padding: 12, background: token.colorBgContainer }}>
            <div style={{ fontWeight: 500 }}>
              {m.question}
              <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                {m.asker_name ?? '—'} · {m.created_at.slice(0, 16).replace('T', ' ')}
              </Typography.Text>
            </div>
            <div style={{ marginTop: 6 }}>
              {(m.status === 'queued' || m.status === 'running') && (
                <span style={{ color: token.colorTextSecondary }}>
                  <Spin size="small" style={{ marginRight: 8 }} />
                  正在根据档案证据回答…
                </span>
              )}
              {m.status === 'completed' && (
                <>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{m.answer}</div>
                  <div style={{ marginTop: 6, fontSize: 12, color: token.colorTextTertiary }}>
                    {m.cited_evidence_ids.length > 0 ? (
                      <>
                        引用：
                        {m.cited_evidence_ids.map((id) => (
                          <Tag key={id}>{citeLabel(id)}</Tag>
                        ))}
                      </>
                    ) : (
                      '没有标出引用的证据。'
                    )}
                    {m.model && <span>模型 {m.model}</span>}
                  </div>
                </>
              )}
              {m.status === 'failed' && (
                <Typography.Text type="danger">没有回答：{ASK_ERROR[m.error_category ?? ''] ?? m.error_category ?? '调用失败'}。可以再问一次。</Typography.Text>
              )}
              {m.status === 'unknown' && (
                <Typography.Text type="warning">
                  结果未知：{ASK_ERROR[m.error_category ?? ''] ?? m.error_category ?? '调用中断'}。系统不会自动重发，可以再问一次。
                </Typography.Text>
              )}
            </div>
          </div>
        ))}
      </div>
      {detail.llm_configured && (
        <div style={{ marginTop: 10 }}>
          {detail.messages.length === 0 && (
            <div style={{ marginBottom: 8 }}>
              {SUGGESTIONS.map((s) => (
                <Tag key={s} style={{ cursor: 'pointer', marginBottom: 4 }} onClick={() => send(s).catch(() => undefined)}>
                  {s}
                </Tag>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <Input.TextArea
              autoSize={{ minRows: 1, maxRows: 4 }}
              maxLength={1000}
              placeholder={detail.asking ? '上一个问题还在回答中…' : '就这份档案的证据提问'}
              disabled={detail.asking}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  send(text).catch(() => undefined);
                }
              }}
            />
            <Button type="primary" icon={<SendOutlined />} loading={sending} disabled={detail.asking || !text.trim()} onClick={() => send(text).catch(() => undefined)}>
              发送
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
