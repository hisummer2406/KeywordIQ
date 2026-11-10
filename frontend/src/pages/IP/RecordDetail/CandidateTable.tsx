import { Button, Col, Descriptions, Image, Modal, Popconfirm, Row, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';
import type { Candidate, DetailKind } from '@/services/risk';
import { DETAIL_STATUS, dt, errorText, safeHttps, ymd } from '../riskLabels';

/**
 * 候选专利表。供应商返回的文本一律作为纯文本渲染（React 默认转义），附图只放行 https。
 * 相似度只用于排序，**不**换算成侵权概率或风险结论。
 */

interface Props {
  candidates: Candidate[];
  originalImage?: string;
  canSearch: boolean;
  detailBusy: boolean;
  maxBatch: number;
  onRequestDetails: (ids: number[]) => Promise<void>;
}

const KIND_TEXT: Record<DetailKind, string> = { bibliography: '著录', legal: '法律状态' };

function DetailTags({ c }: { c: Candidate }) {
  return (
    <Space size={4} wrap>
      {(['bibliography', 'legal'] as DetailKind[]).map((k) => {
        const d = c.details[k];
        if (!d) return <Tag key={k}>{KIND_TEXT[k]}：未获取</Tag>;
        const s = DETAIL_STATUS[d.status];
        const tag = <Tag key={k} color={s.color}>{`${KIND_TEXT[k]}：${s.text}`}</Tag>;
        return d.error_category ? <Tooltip key={k} title={errorText(d.error_category)}>{tag}</Tooltip> : tag;
      })}
    </Space>
  );
}

function list(v: unknown): string {
  return Array.isArray(v) && v.length ? v.filter((x) => typeof x === 'string').join('、') : '—';
}

function DetailView({ c }: { c: Candidate }) {
  const bib = c.details.bibliography?.status === 'success' ? c.details.bibliography.data ?? {} : null;
  const legal = c.details.legal?.status === 'success' ? c.details.legal.data ?? {} : null;
  if (!bib && !legal) {
    return <Typography.Text type="secondary">尚未获取著录与法律状态。</Typography.Text>;
  }
  return (
    <Descriptions size="small" column={2} bordered>
      {bib && (
        <>
          <Descriptions.Item label="名称">{bib.title ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="类型">{bib.patent_type ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="权利人">{list(bib.assignees)}</Descriptions.Item>
          <Descriptions.Item label="申请人">{list(bib.applicants)}</Descriptions.Item>
          <Descriptions.Item label="公开号">{bib.publication_number ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="公开日">{ymd(bib.publication_date)}</Descriptions.Item>
          <Descriptions.Item label="申请号">{bib.application_number ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="申请日">{ymd(bib.application_date)}</Descriptions.Item>
          <Descriptions.Item label="著录取证时间" span={2}>{dt(c.details.bibliography?.fetched_at)}</Descriptions.Item>
        </>
      )}
      {legal && (
        <>
          <Descriptions.Item label="简单法律状态">{list(legal.simple_legal_status)}</Descriptions.Item>
          <Descriptions.Item label="法律状态">{list(legal.legal_status)}</Descriptions.Item>
          <Descriptions.Item label="法律事件">{list(legal.event_status)}</Descriptions.Item>
          <Descriptions.Item label="状态更新日">{ymd(legal.legal_date)}</Descriptions.Item>
          <Descriptions.Item label="法律状态取证时间" span={2}>{dt(c.details.legal?.fetched_at)}</Descriptions.Item>
        </>
      )}
    </Descriptions>
  );
}

export default function CandidateTable({ candidates, originalImage, canSearch, detailBusy, maxBatch, onRequestDetails }: Props) {
  const [selected, setSelected] = useState<number[]>([]);
  const [compare, setCompare] = useState<Candidate | null>(null);
  const [requesting, setRequesting] = useState(false);

  const pending = selected.filter((id) => {
    const c = candidates.find((x) => x.id === id);
    return c && (c.details.bibliography?.status !== 'success' || c.details.legal?.status !== 'success');
  });

  const columns: ColumnsType<Candidate> = [
    { title: '#', dataIndex: 'rank', width: 56 },
    {
      title: '附图',
      key: 'img',
      width: 112,
      render: (_, c) => {
        const src = safeHttps(c.image_url);
        return src ? (
          <Space direction="vertical" size={2}>
            <Image src={src} width={88} alt={c.patent_pn ?? c.patent_id} />
            {c.image_maybe_expired && <Typography.Text type="warning" style={{ fontSize: 12 }}>链接可能已过期</Typography.Text>}
          </Space>
        ) : (
          <Typography.Text type="secondary">无附图</Typography.Text>
        );
      },
    },
    {
      title: '名称 / 公开号',
      key: 'title',
      render: (_, c) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{c.title ?? '—'}</Typography.Text>
          <Typography.Text type="secondary">{c.patent_pn ?? c.patent_id}</Typography.Text>
        </Space>
      ),
    },
    { title: '法域', dataIndex: 'authority', width: 72, render: (v) => v ?? '—' },
    { title: '申请日', dataIndex: 'apdt', width: 110, render: (v) => ymd(v) },
    { title: '公开日', dataIndex: 'pbdt', width: 110, render: (v) => ymd(v) },
    { title: '权利人', dataIndex: 'assignee', render: (v) => v ?? '—' },
    { title: '详情', key: 'details', render: (_, c) => <DetailTags c={c} /> },
    {
      title: '操作',
      key: 'op',
      width: 96,
      render: (_, c) => (
        <Button size="small" onClick={() => setCompare(c)} disabled={!safeHttps(c.image_url) || !originalImage}>
          并排比对
        </Button>
      ),
    },
  ];

  const submit = async () => {
    setRequesting(true);
    try {
      await onRequestDetails(pending);
      setSelected([]);
    } finally {
      setRequesting(false);
    }
  };

  return (
    <>
      {canSearch && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Popconfirm
            title="读取著录与法律状态"
            description={`将对 ${pending.length} 件尚未获取的候选调用 P011、P013 各一次（按成功条数计费）。已获取的不会重复调用。`}
            onConfirm={submit}
            disabled={pending.length === 0 || detailBusy}
          >
            <Button disabled={pending.length === 0 || detailBusy} loading={requesting || detailBusy}>
              {`读取著录与法律状态（${pending.length}）`}
            </Button>
          </Popconfirm>
          <Typography.Text type="secondary">{`一次最多选 ${maxBatch} 件；相似度只用于排序，不代表侵权概率。`}</Typography.Text>
        </Space>
      )}
      <Table<Candidate>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={candidates}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        rowSelection={
          canSearch
            ? {
                selectedRowKeys: selected,
                onChange: (keys) => setSelected((keys as number[]).slice(0, maxBatch)),
                hideSelectAll: true,
              }
            : undefined
        }
        expandable={{ expandedRowRender: (c) => <DetailView c={c} /> }}
        scroll={{ x: 1000 }}
      />
      <Modal open={!!compare} onCancel={() => setCompare(null)} footer={null} width={960} title="并排比对">
        {compare && (
          <Row gutter={24}>
            <Col span={12}>
              <Typography.Title level={5}>产品原图</Typography.Title>
              <Image src={originalImage} width="100%" alt="产品原图" />
            </Col>
            <Col span={12}>
              <Typography.Title level={5}>{compare.patent_pn ?? compare.patent_id}</Typography.Title>
              <Image src={safeHttps(compare.image_url)} width="100%" alt={compare.patent_pn ?? compare.patent_id} />
              <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                {compare.title ?? ''}
                {`（附图取证于 ${dt(compare.fetched_at)}${compare.image_maybe_expired ? '，链接可能已过期' : ''}）`}
              </Typography.Paragraph>
            </Col>
          </Row>
        )}
      </Modal>
    </>
  );
}
