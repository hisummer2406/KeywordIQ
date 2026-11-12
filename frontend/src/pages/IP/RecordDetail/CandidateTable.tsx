import {
  Alert,
  Button,
  Checkbox,
  Col,
  Descriptions,
  Image,
  Modal,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import type { Candidate, DetailKind, EvidenceFile, FileDetailData } from '@/services/risk';
import { getEvidenceFile } from '@/services/risk';
import {
  archiveErrorText,
  DETAIL_KIND_TEXT,
  DETAIL_KINDS,
  DETAIL_STATUS,
  detailBatchLimit,
  detailCallText,
  dt,
  errorText,
  hasFileKind,
  safeHttps,
  ymd,
} from '../riskLabels';

/**
 * 候选专利表。供应商返回的文本一律作为纯文本渲染（React 默认转义），附图只放行 https。
 * 相似度只用于排序，**不**换算成侵权概率或风险结论。
 *
 * 全部附图与公报 PDF（change add-patent-fulltext-evidence）：已存档的经鉴权端点取 Blob 展示，
 * 未存档的只在签名链接有效期内给出链接，并标明「未存档」。
 */

interface Props {
  checkId: number;
  candidates: Candidate[];
  originalImage?: string;
  canSearch: boolean;
  detailBusy: boolean;
  maxBatch: number;
  maxFileBatch: number;
  onRequestDetails: (ids: number[], kinds: DetailKind[]) => Promise<void>;
}

function DetailTags({ c }: { c: Candidate }) {
  return (
    <Space size={4} wrap>
      {DETAIL_KINDS.map((k) => {
        const d = c.details[k];
        if (!d) return <Tag key={k}>{`${DETAIL_KIND_TEXT[k]}：未获取`}</Tag>;
        const s = DETAIL_STATUS[d.status];
        const tag = <Tag key={k} color={s.color}>{`${DETAIL_KIND_TEXT[k]}：${s.text}`}</Tag>;
        return d.error_category ? <Tooltip key={k} title={errorText(d.error_category)}>{tag}</Tooltip> : tag;
      })}
    </Space>
  );
}

function list(v: unknown): string {
  return Array.isArray(v) && v.length ? v.filter((x) => typeof x === 'string').join('、') : '—';
}

function fileData(c: Candidate, kind: 'drawings' | 'pdf'): FileDetailData | null {
  const d = c.details[kind];
  return d?.status === 'success' && d.data ? (d.data as FileDetailData) : null;
}

/** 未存档条目的说明：原因 + 链接有效期。 */
function linkNote(f: EvidenceFile): string {
  const why = archiveErrorText(f.archive_error);
  if (f.link_url) return `未存档（${why}），链接在 ${dt(f.link_expires_at)} 前有效`;
  return `未存档（${why}），链接已失效`;
}

/**
 * 一件候选全部附图的可展示地址：已存档的取 Blob 转对象 URL（卸载时回收），未存档的用有效期内的签名链接。
 */
function useDrawingUrls(checkId: number, c: Candidate | null, enabled: boolean): (string | undefined)[] {
  const files = useMemo(() => (c ? fileData(c, 'drawings')?.files ?? [] : []), [c]);
  const cid = c?.id;
  // 轮询会换掉候选对象；只在已存档的序号集合变化时重新取 Blob，避免每 3 秒重复下载
  const archivedSeqs = files.filter((f) => f.archived).map((f) => f.seq).join(',');
  const [blobs, setBlobs] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!enabled || cid === undefined || !archivedSeqs) return undefined;
    let alive = true;
    const created: string[] = [];
    archivedSeqs
      .split(',')
      .map(Number)
      .forEach((seq) => {
        getEvidenceFile(checkId, cid, 'drawings', seq)
          .then((blob) => {
            const url = URL.createObjectURL(blob);
            created.push(url);
            if (alive) setBlobs((prev) => ({ ...prev, [seq]: url }));
            else URL.revokeObjectURL(url);
          })
          .catch(() => undefined);
      });
    return () => {
      alive = false;
      created.forEach((u) => URL.revokeObjectURL(u));
      setBlobs({});
    };
  }, [checkId, cid, archivedSeqs, enabled]);

  return files.map((f) => (f.archived ? blobs[f.seq] : safeHttps(f.link_url)));
}

function DrawingsView({ checkId, c }: { checkId: number; c: Candidate }) {
  const data = fileData(c, 'drawings');
  const urls = useDrawingUrls(checkId, c, !!data);
  if (!data) return null;
  const unarchived = data.files.filter((f) => !f.archived);
  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Typography.Text strong>{`全部附图（${data.files.length} 张，取证于 ${dt(c.details.drawings?.fetched_at)}）`}</Typography.Text>
      <Image.PreviewGroup>
        <Space size={8} wrap>
          {data.files.map((f, i) =>
            urls[i] ? (
              <Image key={f.seq} src={urls[i]} width={96} alt={`附图 ${f.seq}`} />
            ) : (
              <Tooltip key={f.seq} title={f.archived ? '加载中' : linkNote(f)}>
                <div
                  style={{
                    width: 96,
                    height: 96,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    border: '1px dashed #d9d9d9',
                    fontSize: 12,
                  }}
                >
                  {f.archived ? '加载中' : `附图 ${f.seq}`}
                </div>
              </Tooltip>
            ),
          )}
        </Space>
      </Image.PreviewGroup>
      {unarchived.length > 0 && (
        <Typography.Text type="warning" style={{ fontSize: 12 }}>
          {`${unarchived.length} 张${linkNote(unarchived[0])}`}
        </Typography.Text>
      )}
    </Space>
  );
}

function PdfView({ checkId, c }: { checkId: number; c: Candidate }) {
  const data = fileData(c, 'pdf');
  const [opening, setOpening] = useState(false);
  const f = data?.files[0];
  if (!f) return null;

  const open = async () => {
    // 先同步开窗，避免异步取回后被浏览器拦截弹窗
    const win = window.open('', '_blank');
    setOpening(true);
    try {
      const blob = await getEvidenceFile(checkId, c.id, 'pdf', f.seq);
      const url = URL.createObjectURL(blob);
      if (win) win.location.href = url;
      else window.location.assign(url);
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      win?.close();
    } finally {
      setOpening(false);
    }
  };

  return (
    <Space size={8} wrap>
      <Typography.Text strong>公报 PDF</Typography.Text>
      {f.archived ? (
        <Button size="small" loading={opening} onClick={open}>
          {`打开公报 PDF${data?.pn ? `（${data.pn}）` : ''}`}
        </Button>
      ) : f.link_url ? (
        <a href={f.link_url} target="_blank" rel="noopener noreferrer">
          打开公报 PDF
        </a>
      ) : null}
      {!f.archived && (
        <Typography.Text type="warning" style={{ fontSize: 12 }}>
          {linkNote(f)}
        </Typography.Text>
      )}
    </Space>
  );
}

function DetailView({ checkId, c }: { checkId: number; c: Candidate }) {
  const bib = c.details.bibliography?.status === 'success' ? c.details.bibliography.data ?? {} : null;
  const legal = c.details.legal?.status === 'success' ? c.details.legal.data ?? {} : null;
  const hasFiles = !!fileData(c, 'drawings') || !!fileData(c, 'pdf');
  if (!bib && !legal && !hasFiles) {
    return <Typography.Text type="secondary">尚未获取详情。</Typography.Text>;
  }
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {(bib || legal) && (
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
      )}
      <DrawingsView checkId={checkId} c={c} />
      <PdfView checkId={checkId} c={c} />
    </Space>
  );
}

/** 并排比对：右侧默认检索命中图，已获取全部附图时可切换到任一视图。 */
function CompareModal({
  checkId,
  c,
  originalImage,
  onClose,
}: {
  checkId: number;
  c: Candidate | null;
  originalImage?: string;
  onClose: () => void;
}) {
  const urls = useDrawingUrls(checkId, c, !!c);
  const [sel, setSel] = useState<number>(-1);
  useEffect(() => setSel(-1), [c]);
  if (!c) return null;
  const hit = safeHttps(c.image_url);
  const right = sel >= 0 ? urls[sel] : hit;
  const label = c.patent_pn ?? c.patent_id;
  return (
    <Modal open onCancel={onClose} footer={null} width={960} title="并排比对">
      <Row gutter={24}>
        <Col span={12}>
          <Typography.Title level={5}>产品原图</Typography.Title>
          <Image src={originalImage} width="100%" alt="产品原图" />
        </Col>
        <Col span={12}>
          <Typography.Title level={5}>{label}</Typography.Title>
          {right ? (
            <Image src={right} width="100%" alt={label} />
          ) : (
            <Typography.Text type="secondary">该视图不可用</Typography.Text>
          )}
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            {c.title ?? ''}
            {sel < 0
              ? `（检索命中图，取证于 ${dt(c.fetched_at)}${c.image_maybe_expired ? '，链接可能已过期' : ''}）`
              : `（全部附图第 ${sel + 1} 张，取证于 ${dt(c.details.drawings?.fetched_at)}）`}
          </Typography.Paragraph>
        </Col>
      </Row>
      {urls.length > 0 && (
        <Space size={8} wrap style={{ marginTop: 12 }}>
          {hit && (
            <Button size="small" type={sel < 0 ? 'primary' : 'default'} onClick={() => setSel(-1)}>
              命中图
            </Button>
          )}
          {urls.map((u, i) => (
            <Button key={i} size="small" type={sel === i ? 'primary' : 'default'} disabled={!u} onClick={() => setSel(i)}>
              {`附图 ${i + 1}`}
            </Button>
          ))}
        </Space>
      )}
    </Modal>
  );
}

function needs(c: Candidate, k: DetailKind): boolean {
  return c.details[k]?.status !== 'success';
}

export default function CandidateTable({
  checkId,
  candidates,
  originalImage,
  canSearch,
  detailBusy,
  maxBatch,
  maxFileBatch,
  onRequestDetails,
}: Props) {
  const [selected, setSelected] = useState<number[]>([]);
  const [compare, setCompare] = useState<Candidate | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [picking, setPicking] = useState(false);
  const [kinds, setKinds] = useState<DetailKind[]>(['bibliography', 'legal']);

  const chosen = candidates.filter((c) => selected.includes(c.id));
  const limit = detailBatchLimit(kinds, maxBatch, maxFileBatch);
  const overLimit = chosen.length > limit;
  const count: Partial<Record<DetailKind, number>> = {};
  kinds.forEach((k) => {
    count[k] = chosen.filter((c) => needs(c, k)).length;
  });
  const ids = chosen.filter((c) => kinds.some((k) => needs(c, k))).map((c) => c.id);

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
    { title: '详情', key: 'details', width: 220, render: (_, c) => <DetailTags c={c} /> },
    {
      title: '操作',
      key: 'op',
      width: 96,
      render: (_, c) => (
        <Button
          size="small"
          onClick={() => setCompare(c)}
          disabled={!originalImage || (!safeHttps(c.image_url) && !fileData(c, 'drawings'))}
        >
          并排比对
        </Button>
      ),
    },
  ];

  const submit = async () => {
    setRequesting(true);
    try {
      await onRequestDetails(ids, kinds);
      setSelected([]);
      setPicking(false);
    } finally {
      setRequesting(false);
    }
  };

  return (
    <>
      {canSearch && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Button disabled={selected.length === 0 || detailBusy} loading={detailBusy} onClick={() => setPicking(true)}>
            {`补充详情（已选 ${selected.length}）`}
          </Button>
          <Typography.Text type="secondary">
            {`一次最多选 ${maxBatch} 件，含全部附图或公报时最多 ${maxFileBatch} 件；相似度只用于排序，不代表侵权概率。`}
          </Typography.Text>
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
        expandable={{ expandedRowRender: (c) => <DetailView checkId={checkId} c={c} /> }}
        scroll={{ x: 1100 }}
      />
      <Modal
        open={picking}
        title={`补充详情（已选 ${chosen.length} 件）`}
        onCancel={() => setPicking(false)}
        onOk={submit}
        okText="确认获取"
        okButtonProps={{ disabled: ids.length === 0 || overLimit || kinds.length === 0, loading: requesting }}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Checkbox.Group
            value={kinds}
            onChange={(v) => setKinds(DETAIL_KINDS.filter((k) => (v as DetailKind[]).includes(k)))}
            options={DETAIL_KINDS.map((k) => ({ label: DETAIL_KIND_TEXT[k], value: k }))}
          />
          {overLimit ? (
            <Alert type="warning" showIcon message={`含全部附图或公报 PDF 时一次最多 ${limit} 件，请减少勾选。`} />
          ) : (
            <Typography.Paragraph style={{ marginBottom: 0 }}>{detailCallText(count)}</Typography.Paragraph>
          )}
          {hasFileKind(kinds) && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              全部附图与公报会存档，之后查看不再调用接口；存档失败时只保留约 7–10 天有效的供应商链接。
            </Typography.Text>
          )}
        </Space>
      </Modal>
      <CompareModal checkId={checkId} c={compare} originalImage={originalImage} onClose={() => setCompare(null)} />
    </>
  );
}
