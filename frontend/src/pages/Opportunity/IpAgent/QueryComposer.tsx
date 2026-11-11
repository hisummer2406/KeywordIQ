import { Alert, Button, Input, Popconfirm, Select, Space, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { requestSearch, type ResearchDetail } from '@/services/opportunityIp';
import { newToken } from '@/services/risk';

/**
 * 确认要执行的检索式（决策 1）。Agent 的方案只填进这里，**不会自动执行**；只有点击「执行检索」
 * 并确认调用次数后才入队。在对话里以「待你确认」卡片出现。
 */

interface Props {
  data: ResearchDetail;
  /** 预填的检索式（来自 Agent 方案或空白） */
  initial: string[];
  queryLimits: number[];
  maxQueries: number;
  busy: boolean;
  onQueued: () => void;
  onCancel: () => void;
}

interface Draft {
  key: number;
  query_text: string;
  limit: number;
}

let seq = 0;
const draft = (query_text = '', limit = 20): Draft => ({ key: ++seq, query_text, limit });

export const SYNTAX_HELP =
  '已实测可用：TTL:(标题词)、TA:(标题或摘要词)、LOC:(07-01) 只命中外观设计、AUTHORITY:(US OR EU OR WO)、' +
  'APD:[20150101 TO 20261231]，用 AND / OR / NOT 组合。PT:(D) 是语法错误，不要使用。';

export default function QueryComposer({ data, initial, queryLimits, maxQueries, busy, onQueued, onCancel }: Props) {
  const [drafts, setDrafts] = useState<Draft[]>(() => (initial.length ? initial.map((q) => draft(q)) : [draft()]));
  const [sending, setSending] = useState(false);
  const token = useRef(newToken());
  const remaining = data.budget.per_research - data.budget.used;

  // 预填内容换了（用户点了另一份方案）才覆盖
  const initialKey = initial.join('\n');
  useEffect(() => {
    if (initial.length) setDrafts(initial.map((q) => draft(q)));
    token.current = newToken();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialKey]);

  const valid = drafts.filter((d) => d.query_text.trim());
  const update = (key: number, patch: Partial<Draft>) => {
    token.current = newToken();
    setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  };

  const run = async () => {
    setSending(true);
    try {
      await requestSearch(data.id, {
        client_token: token.current,
        queries: valid.map((d) => ({ query_text: d.query_text.trim(), limit: d.limit })),
      });
      token.current = newToken();
      onQueued();
    } finally {
      setSending(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={10}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {SYNTAX_HELP}
      </Typography.Text>
      {drafts.map((d) => (
        <div key={d.key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          <Input.TextArea
            style={{ flex: 1 }}
            autoSize={{ minRows: 1, maxRows: 4 }}
            maxLength={500}
            value={d.query_text}
            placeholder="TTL:(mug OR cup) AND LOC:(07-01) AND AUTHORITY:(US)"
            onChange={(e) => update(d.key, { query_text: e.target.value })}
          />
          <Select
            style={{ width: 112 }}
            value={d.limit}
            options={queryLimits.map((n) => ({ value: n, label: `取前 ${n} 条` }))}
            onChange={(limit) => update(d.key, { limit })}
          />
          {drafts.length > 1 && (
            <Button type="link" onClick={() => setDrafts((ds) => ds.filter((x) => x.key !== d.key))}>
              删除
            </Button>
          )}
        </div>
      ))}
      {!data.mcp_configured && <Alert type="warning" showIcon message="智慧芽接口未配置，无法执行检索。" />}
      <Space wrap>
        <Button disabled={drafts.length >= maxQueries} onClick={() => setDrafts((ds) => [...ds, draft()])}>
          添加一条
        </Button>
        <Popconfirm
          title={`将调用智慧芽 MCP ${valid.length} 次`}
          description={`本调研预算剩余 ${remaining} 次；失败或结果未知的调用也会计入。`}
          okText="确认执行"
          onConfirm={run}
          disabled={!valid.length || busy || !data.mcp_configured}
        >
          <Button type="primary" disabled={!valid.length || busy || !data.mcp_configured} loading={sending}>
            {`执行检索（${valid.length} 次调用）`}
          </Button>
        </Popconfirm>
        <Button type="text" onClick={onCancel}>
          收起
        </Button>
      </Space>
    </Space>
  );
}
