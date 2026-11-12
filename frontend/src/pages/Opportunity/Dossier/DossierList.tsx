import { PlusOutlined } from '@ant-design/icons';
import { Badge, Button, Input, Radio, Select, Table, Tag, Typography, theme } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';
import { type DossierListItem, type DossierListResult, type DossierStatus, listDossiers } from '@/services/opportunityDossier';
import { isDue, STATUS } from './dossierView';

/**
 * 机会档案列表（机会中心默认视图，opportunity-center「机会档案是机会中心的默认入口」）。
 * 档案团队可见，所以这里列的是全团队的档案；「我负责」「待复查」是筛选，不是权限。
 */

type Scope = 'all' | 'due' | 'mine' | 'archived';

interface Props {
  onOpen: (id: number) => void;
  onNew: () => void;
}

const PAGE_SIZE = 20;

export default function DossierList({ onOpen, onNew }: Props) {
  const { token } = theme.useToken();
  const [scope, setScope] = useState<Scope>('all');
  const [status, setStatus] = useState<DossierStatus | undefined>();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<DossierListResult | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(
        await listDossiers({
          page,
          page_size: PAGE_SIZE,
          ...(status ? { status } : {}),
          ...(scope === 'due' ? { due: true } : {}),
          ...(scope === 'mine' ? { mine: true } : {}),
          ...(scope === 'archived' ? { archived: true } : {}),
          ...(q.trim() ? { q: q.trim() } : {}),
        }),
      );
    } finally {
      setLoading(false);
    }
  }, [page, scope, status, q]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const today = data?.today ?? '';
  const columns: ColumnsType<DossierListItem> = [
    {
      title: '档案',
      dataIndex: 'title',
      render: (_, r) => (
        <div style={{ minWidth: 0 }}>
          <Typography.Text strong>{r.title}</Typography.Text>
          {r.idea && (
            <Typography.Paragraph type="secondary" ellipsis={{ rows: 1 }} style={{ margin: 0, fontSize: 12, maxWidth: 420 }}>
              {r.idea}
            </Typography.Paragraph>
          )}
        </div>
      ),
    },
    { title: '站点', dataIndex: 'site', width: 70 },
    { title: '关键词', dataIndex: 'keyword_count', width: 80, align: 'right' },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: DossierStatus) => <Tag color={STATUS[s].color}>{STATUS[s].text}</Tag> },
    {
      title: '复查日期',
      dataIndex: 'recheck_date',
      width: 130,
      render: (d: string | null) =>
        d ? isDue(d, today) ? <Badge status="error" text={<span style={{ color: token.colorError }}>{d} 到期</span>} /> : d : '—',
    },
    { title: '负责人', dataIndex: 'assignee_name', width: 110, render: (n: string | null) => n ?? '—' },
    { title: '最近更新', dataIndex: 'updated_at', width: 150, render: (t: string) => t.slice(0, 16).replace('T', ' ') },
  ];

  return (
    <div style={{ padding: 24, height: '100%', overflowY: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
        <Typography.Title level={4} style={{ margin: 0, flex: 1 }}>
          机会档案
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={onNew}>
          新建档案
        </Button>
      </div>
      <Typography.Paragraph type="secondary">
        把一个产品想法和它的一组关键词放进档案：看 ABA 证据、补上缺的资料、查专利、写下决定和复查日期。档案全团队可见。
      </Typography.Paragraph>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={scope}
          onChange={(e) => {
            setScope(e.target.value);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'due', label: `待复查${data?.due_count ? ` ${data.due_count}` : ''}` },
            { value: 'mine', label: '我负责' },
            { value: 'archived', label: '已归档' },
          ]}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          options={(Object.keys(STATUS) as DossierStatus[]).map((s) => ({ value: s, label: STATUS[s].text }))}
        />
        <Input.Search
          allowClear
          placeholder="搜标题或关键词"
          style={{ width: 240 }}
          onSearch={(v) => {
            setQ(v);
            setPage(1);
          }}
        />
      </div>
      <Table<DossierListItem>
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={data?.items ?? []}
        onRow={(r) => ({ onClick: () => onOpen(r.id), style: { cursor: 'pointer' } })}
        pagination={{ current: page, pageSize: PAGE_SIZE, total: data?.total ?? 0, showSizeChanger: false, onChange: setPage }}
        locale={{
          emptyText:
            scope === 'all' && !status && !q ? '还没有档案。从一个想做的产品和它的几个关键词开始。' : '没有符合条件的档案',
        }}
      />
    </div>
  );
}
