import { Bubble, type BubbleItemType, Prompts, Sender } from '@ant-design/x';
import { Alert, Space, Spin, Typography } from 'antd';
import { useState } from 'react';
import type { DossierDetail } from '@/services/opportunityDossier';
import AnswerMarkdown from '../chat/AnswerMarkdown';
import { CitationTags, MetaLine, useChatRoles } from '../chat/roles';
import { ASK_ERROR } from './dossierView';

/**
 * 追问。大模型只拿到这份档案里的证据（ABA 快照、有效的人工证据、决定、专利摘要），
 * 档案里没有的数据它应当直说没有。回答下方标出引用了哪些证据，便于回到上面核对。
 *
 * 与 IP 专家任务对话共用消息规范（change refine-opportunity-chat-ui 决策 D1、D5）：问在右、答在左，
 * 回答走受限 Markdown。嵌在档案详情的长页面里，所以消息列表不另设滚动区。
 */

interface Props {
  detail: DossierDetail;
  onAsk: (question: string) => Promise<void>;
}

/** 追问最长字数（与原输入框 maxLength 一致） */
const ASK_MAX = 1000;
const SUGGESTIONS = [
  '这几个词的头部商品稳定吗？',
  '现有证据支持什么、还缺什么？',
  '如果要推进，下一步最该先核实什么？',
];

export default function AskPanel({ detail, onAsk }: Props) {
  const roles = useChatRoles('research');
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
  const manualIds = new Set(
    [...detail.gaps.flatMap((g) => g.evidence), ...detail.other_evidence].map((e) => e.id),
  );
  const citeLabel = (id: number) =>
    manualIds.has(id)
      ? `人工证据 #${id}`
      : id === snapshotId
        ? 'ABA 证据快照（当前）'
        : `ABA 证据快照 #${id}（较早）`;

  const items: BubbleItemType[] = detail.messages.flatMap((m): BubbleItemType[] => {
    const at = m.created_at.slice(0, 16).replace('T', ' ');
    const question: BubbleItemType = {
      key: `q-${m.id}`,
      role: 'user',
      content: m.question,
      footer: <MetaLine parts={[m.asker_name ?? '—', at]} />,
    };
    if (m.status === 'queued' || m.status === 'running') {
      return [
        question,
        {
          key: `a-${m.id}`,
          role: 'ai',
          content: '',
          loading: true,
          loadingRender: () => (
            <Space>
              <Spin size="small" />
              <Typography.Text type="secondary">正在根据档案证据回答…</Typography.Text>
            </Space>
          ),
        },
      ];
    }
    if (m.status === 'completed') {
      return [
        question,
        {
          key: `a-${m.id}`,
          role: 'ai',
          content: (
            <>
              <AnswerMarkdown>{m.answer ?? ''}</AnswerMarkdown>
              <div style={{ marginTop: 8 }}>
                {m.cited_evidence_ids.length > 0 ? (
                  <CitationTags labels={m.cited_evidence_ids.map(citeLabel)} />
                ) : (
                  <span className="chat-meta">没有标出引用的证据。</span>
                )}
              </div>
            </>
          ),
          footer: <MetaLine parts={['大模型回答，仅供参考', m.model && `模型 ${m.model}`]} />,
        },
      ];
    }
    const reason = ASK_ERROR[m.error_category ?? ''] ?? m.error_category;
    return [
      question,
      {
        key: `a-${m.id}`,
        role: 'ai',
        content:
          m.status === 'failed' ? (
            <Typography.Text type="danger">
              没有回答：{reason ?? '调用失败'}。可以再问一次。
            </Typography.Text>
          ) : (
            <Typography.Text type="warning">
              结果未知：{reason ?? '调用中断'}。系统不会自动重发，可以再问一次。
            </Typography.Text>
          ),
      },
    ];
  });

  return (
    <div>
      <Typography.Title level={5} style={{ marginBottom: 4 }}>
        追问
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
        回答只基于这份档案里的证据，不查外部数据，也不替你做决定。它是大模型生成的，引用的证据请回到上面核对。
      </Typography.Paragraph>
      {!detail.llm_configured && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 10 }}
          title="大模型未配置，暂时不能追问。证据、补证与决定都不受影响。"
        />
      )}
      {items.length > 0 && (
        <div style={{ maxWidth: 880, marginBottom: 12 }}>
          <Bubble.List autoScroll={false} items={items} role={roles} />
        </div>
      )}
      {detail.llm_configured && (
        <div style={{ maxWidth: 880 }}>
          {detail.messages.length === 0 && (
            <Prompts
              style={{ marginBottom: 8 }}
              wrap
              items={SUGGESTIONS.map((s) => ({ key: s, label: s, disabled: sending }))}
              onItemClick={({ data }) => send(String(data.key)).catch(() => undefined)}
            />
          )}
          <Sender
            value={text}
            onChange={(v) => setText(v.slice(0, ASK_MAX))}
            onSubmit={(v) => send(v).catch(() => undefined)}
            loading={sending}
            disabled={detail.asking}
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder={detail.asking ? '上一个问题还在回答中…' : '就这份档案的证据提问'}
          />
        </div>
      )}
    </div>
  );
}
