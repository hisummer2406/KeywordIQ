import { Button, Card, Checkbox, Image, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { DETAIL_STATUS, ymd } from '@/pages/IP/riskLabels';
import {
  type DetailTool,
  getCandidateImage,
  type IpCandidate,
  requestDetails,
  type ResearchDetail,
} from '@/services/opportunityIp';
import { newToken } from '@/services/risk';
import { mcpErrorText, TOOL_TEXT } from './ipLabels';

/**
 * 候选与取证（决策 6）。供应商文本一律作为纯文本渲染；附图来自鉴权端点（取证时已存库）。
 * 只对尚无成功结果的「候选 × 工具」计数和调用。
 */

interface Props {
  data: ResearchDetail;
  tools: DetailTool[];
  maxBatch: number;
  busy: boolean;
  onQueued: () => void;
}

function Thumb({ researchId, c }: { researchId: number; c: IpCandidate }) {
  const [url, setUrl] = useState<string>();
  const has = c.details.abstract_image?.has_image;
  useEffect(() => {
    if (!has) return undefined;
    let obj: string | undefined;
    getCandidateImage(researchId, c.id)
      .then((blob) => {
        obj = URL.createObjectURL(blob);
        setUrl(obj);
      })
      .catch(() => undefined);
    return () => {
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [researchId, c.id, has]);
  if (!url) return <Typography.Text type="secondary">—</Typography.Text>;
  return <Image src={url} width={64} height={64} style={{ objectFit: 'contain' }} />;
}

function DetailTags({ c, tools }: { c: IpCandidate; tools: DetailTool[] }) {
  return (
    <Space size={4} wrap>
      {tools.map((t) => {
        const d = c.details[t];
        if (!d) return <Tag key={t}>{`${TOOL_TEXT[t]}：未获取`}</Tag>;
        const s = DETAIL_STATUS[d.status];
        let text = `${TOOL_TEXT[t]}：${s.text}`;
        if (t === 'get_patent_legal_status' && d.status === 'success') {
          const v = d.data?.simple_legal_status;
          text = `法律状态：${Array.isArray(v) && v.length ? v.join('、') : '来源未给出'}`;
        }
        if (t === 'abstract_image' && d.status === 'success' && !d.has_image) text = '摘要附图：图片下载失败';
        const tag = <Tag key={t} color={s.color}>{text}</Tag>;
        return d.error_category ? <Tooltip key={t} title={mcpErrorText(d.error_category)}>{tag}</Tooltip> : tag;
      })}
    </Space>
  );
}

export default function CandidatePanel({ data, tools, maxBatch, busy, onQueued }: Props) {
  const [selected, setSelected] = useState<number[]>([]);
  const [picked, setPicked] = useState<DetailTool[]>(tools);
  const [sending, setSending] = useState(false);
  const token = useRef(newToken());
  const queryNo = new Map(data.queries.map((q, i) => [q.id, i + 1]));

  const needed = selected.reduce((n, id) => {
    const c = data.candidates.find((x) => x.id === id);
    return n + picked.filter((t) => c?.details[t]?.status !== 'success').length;
  }, 0);

  const fetch = async () => {
    setSending(true);
    try {
      await requestDetails(data.id, { client_token: token.current, candidate_ids: selected, tools: picked });
      token.current = newToken();
      setSelected([]);
      onQueued();
    } finally {
      setSending(false);
    }
  };

  return (
    <Card
      size="small"
      title={`候选专利（${data.candidates.length}）`}
      extra={
        <Space>
          <Checkbox.Group
            value={picked}
            options={tools.map((t) => ({ value: t, label: TOOL_TEXT[t] }))}
            onChange={(v) => {
              token.current = newToken();
              setPicked(v as DetailTool[]);
            }}
          />
          <Popconfirm
            title={`将调用智慧芽 MCP ${needed} 次`}
            description="已取得的详情不会重复调用；失败或结果未知的调用也会计入预算。"
            okText="确认取证"
            onConfirm={fetch}
            disabled={!needed || busy || !data.mcp_configured}
          >
            <Button disabled={!needed || busy || !data.mcp_configured} loading={sending}>
              {`取证所选（${selected.length}/${maxBatch}）`}
            </Button>
          </Popconfirm>
        </Space>
      }
    >
      {data.candidates.length === 0 ? (
        <Typography.Text type="secondary">
          {data.search_done ? '已执行的检索在其范围内未找到候选（不代表没有风险）。' : '尚未成功执行任何检索。'}
        </Typography.Text>
      ) : (
        <Table<IpCandidate>
          rowKey="id"
          size="small"
          dataSource={data.candidates}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => {
              token.current = newToken();
              setSelected((keys as number[]).slice(0, maxBatch));
            },
          }}
          columns={[
            { title: '附图', width: 80, render: (_: unknown, c) => <Thumb researchId={data.id} c={c} /> },
            {
              title: '专利',
              render: (_: unknown, c) => (
                <Space orientation="vertical" size={0}>
                  <Typography.Text strong>{c.pn ?? c.patent_id}</Typography.Text>
                  <Typography.Text>{c.title ?? '—'}</Typography.Text>
                  <Typography.Text type="secondary">{`${c.authority ?? '—'} · 申请 ${ymd(c.apdt)} · 公开 ${ymd(c.pbdt)}`}</Typography.Text>
                </Space>
              ),
            },
            { title: '权利人', dataIndex: 'assignee', width: 180, render: (v: string | null) => v ?? '—' },
            {
              title: '来源',
              width: 110,
              render: (_: unknown, c) => `检索式 ${queryNo.get(c.query_id) ?? '?'} · 第 ${c.rank} 条`,
            },
            { title: '证据', width: 300, render: (_: unknown, c) => <DetailTags c={c} tools={tools} /> },
          ]}
        />
      )}
    </Card>
  );
}
