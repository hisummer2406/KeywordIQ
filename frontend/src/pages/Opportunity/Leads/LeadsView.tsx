import { Alert, Button, Modal, Select, Space, Table, Tag, Typography, message, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';
import TrendSparkline from '@/components/TrendSparkline';
import { addDossierKeywords, type DossierListItem, listDossiers } from '@/services/opportunityDossier';
import { type GrowthLead, type GrowthLeadsResult, listGrowthLeads } from '@/services/opportunityLeads';
import { formatRank, STATUS } from '../Dossier/dossierView';

/**
 * 线索（信号洞察专家，spec opportunity-leads）。持续增长榜的词 + 它们已经在哪些档案里；
 * 勾选后一步建档或并入已有档案。榜单口径来自响应里的 criteria，不在这里写死。
 */

const PAGE_SIZE = 50;
const MAX_PICK = 20;

interface Props {
  sites: { code: string; name: string }[];
  onOpenDossier: (id: number) => void;
  onNewDossier: (preset: { site: string; keywords: string[] }) => void;
}

export default function LeadsView({ sites, onOpenDossier, onNewDossier }: Props) {
  const { token } = theme.useToken();
  const [site, setSite] = useState<string | undefined>(sites[0]?.code);
  const [date, setDate] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<GrowthLeadsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<GrowthLead[]>([]);
  const [merging, setMerging] = useState(false);
  const [targets, setTargets] = useState<DossierListItem[]>([]);
  const [target, setTarget] = useState<number | undefined>();
  const [saving, setSaving] = useState(false);

  // 站点列表晚于首屏到达时补上默认站点
  useEffect(() => {
    if (!site && sites[0]) setSite(sites[0].code);
  }, [site, sites]);

  const load = useCallback(async () => {
    if (!site) return;
    setLoading(true);
    try {
      setData(await listGrowthLeads({ site, ...(date ? { date } : {}), page, page_size: PAGE_SIZE }));
    } finally {
      setLoading(false);
    }
  }, [site, date, page]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const searchTargets = useCallback(
    async (q: string) => {
      const r = await listDossiers({ page: 1, page_size: 50, ...(q.trim() ? { q: q.trim() } : {}) });
      // 关键词按站点解析，只能并入同站点的档案
      setTargets(r.items.filter((d) => d.site === data?.site));
    },
    [data?.site],
  );

  const openMerge = () => {
    setTarget(undefined);
    setMerging(true);
    searchTargets('').catch(() => undefined);
  };

  const merge = async () => {
    if (!target) return;
    setSaving(true);
    try {
      const r = await addDossierKeywords(target, picked.map((p) => p.keyword));
      message.success(r.added ? `已并入 ${r.added} 个词，档案证据已更新` : '这些词已经在该档案里');
      setMerging(false);
      setPicked([]);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const criteria = data?.criteria;
  const columns: ColumnsType<GrowthLead> = [
    { title: '关键词', dataIndex: 'keyword', render: (k: string) => <Typography.Text copyable>{k}</Typography.Text> },
    {
      title: '排名（窗口首期 → 锚定日）',
      width: 220,
      render: (_, r) => (
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>
          {formatRank(r.start_ranking)} → <b>{formatRank(r.end_ranking)}</b>
        </span>
      ),
    },
    {
      title: '净提升',
      dataIndex: 'rank_gain',
      width: 110,
      align: 'right',
      render: (g: number) => <span style={{ color: token.colorSuccess, fontVariantNumeric: 'tabular-nums' }}>↑ {formatRank(g)}</span>,
    },
    {
      title: '最小日环比',
      dataIndex: 'min_growth_rate',
      width: 100,
      align: 'right',
      render: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
    { title: '窗口走势', width: 230, render: (_, r) => <TrendSparkline view="DAY" values={r.trend} dates={r.trend_dates} /> },
    {
      title: '已在档案',
      width: 240,
      render: (_, r) =>
        r.dossiers.length === 0 ? (
          <Typography.Text type="secondary">还没有人研究</Typography.Text>
        ) : (
          <Space size={[4, 4]} wrap>
            {r.dossiers.map((d) => (
              <Tag key={d.id} color={STATUS[d.status].color} style={{ cursor: 'pointer' }} onClick={() => onOpenDossier(d.id)}>
                {d.title} · {STATUS[d.status].text}
              </Tag>
            ))}
          </Space>
        ),
    },
  ];

  return (
    <div style={{ padding: 24, height: '100%', overflowY: 'auto' }}>
      <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 4 }}>
        线索
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        信号洞察专家把持续增长榜上的词列在这里，并标出哪些已经有人在研究。勾选一个或几个相关的词，建成机会档案，或并入已有档案。
      </Typography.Paragraph>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        <Select
          style={{ width: 160 }}
          value={site}
          placeholder="站点"
          options={sites.map((s) => ({ value: s.code, label: `${s.name}（${s.code}）` }))}
          onChange={(v) => {
            setSite(v);
            setDate(undefined);
            setPage(1);
            setPicked([]);
          }}
        />
        <Select
          style={{ width: 160 }}
          value={data?.date ?? undefined}
          placeholder="锚定日"
          options={(data?.available_dates ?? []).map((d) => ({ value: d, label: d }))}
          onChange={(v) => {
            setDate(v);
            setPage(1);
            setPicked([]);
          }}
        />
        <span style={{ flex: 1 }} />
        <Typography.Text type="secondary">
          已选 {picked.length} / {MAX_PICK}
        </Typography.Text>
        <Button disabled={picked.length === 0} onClick={openMerge}>
          并入已有档案
        </Button>
        <Button
          type="primary"
          disabled={picked.length === 0 || !data}
          onClick={() => data && onNewDossier({ site: data.site, keywords: picked.map((p) => p.keyword) })}
        >
          用所选词建档
        </Button>
      </div>
      {criteria && (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          口径（导入时算定，与「关键词工具 › 持续增长」是同一份榜单）：近 {criteria.window_days} 期日报全部在榜，累计净提升超过{' '}
          {formatRank(criteria.min_rank_gain)} 名，且每个日环比不低于 {(criteria.min_daily_rate * 100).toFixed(0)}%。排名是搜索频率排名，不是销量。
        </Typography.Paragraph>
      )}
      {data && data.missing_dates.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`窗口内有期次未导入（${data.missing_dates.join('、')}）：所有词都凑不满窗口，榜单可能为空。这不代表今天没有走强的词。`}
        />
      )}
      <Table<GrowthLead>
        rowKey="keyword_id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={data?.items ?? []}
        rowSelection={{
          selectedRowKeys: picked.map((p) => p.keyword_id),
          preserveSelectedRowKeys: true,
          getCheckboxProps: (r) => ({ disabled: picked.length >= MAX_PICK && !picked.some((p) => p.keyword_id === r.keyword_id) }),
          hideSelectAll: true,
          onChange: (_, rows) => setPicked(rows.slice(0, MAX_PICK)),
        }}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: data?.total ?? 0, showSizeChanger: false, onChange: setPage }}
        locale={{
          emptyText: !data?.date
            ? '这个站点还没有导入日报'
            : data.missing_dates.length > 0
              ? '窗口内有期次未导入，暂时算不出榜单'
              : `${data.date} 没有符合口径的词`,
        }}
      />

      <Modal
        open={merging}
        title={`把 ${picked.length} 个词并入已有档案`}
        okText="并入并刷新证据"
        okButtonProps={{ disabled: !target, loading: saving }}
        onOk={() => merge().catch(() => undefined)}
        onCancel={() => setMerging(false)}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          只列出 {data?.site} 站点、未归档的档案。一个档案最多 20 个词，已在档案里的词不会重复添加。
        </Typography.Paragraph>
        <Select
          style={{ width: '100%' }}
          showSearch
          filterOption={false}
          placeholder="搜标题或关键词"
          value={target}
          onChange={setTarget}
          onSearch={(q) => searchTargets(q).catch(() => undefined)}
          options={targets.map((d) => ({ value: d.id, label: `${d.title}（${STATUS[d.status].text} · ${d.keyword_count} 个词）` }))}
          notFoundContent="没有符合条件的档案"
        />
        <div style={{ marginTop: 10, fontSize: 12, color: token.colorTextSecondary }}>{picked.map((p) => p.keyword).join('、')}</div>
      </Modal>
    </div>
  );
}
