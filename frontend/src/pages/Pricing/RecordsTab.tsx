import { Button, Drawer, Input, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';
import {
  copySheet,
  deleteSheet,
  exportVersion,
  getSheet,
  listSheets,
  type PricingMeta,
  type Sheet,
} from '@/services/pricing';

/**
 * 核价记录：只列当前用户自己的（后端按 owner 过滤，他人记录一律 404）。
 *
 * 「规则已更新」由后端比较快照引用的规则与当前生效版本得出；历史金额不变，提示重新核价。
 */

interface Props {
  meta: PricingMeta;
  reloadKey: number;
  onOpen: (sheetId: number, versionId?: number | null) => void;
}

export default function RecordsTab({ meta, reloadKey, onOpen }: Props) {
  const [rows, setRows] = useState<Sheet[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [q, setQ] = useState('');
  const [market, setMarket] = useState<string | undefined>();
  const [status, setStatus] = useState<'draft' | 'saved' | undefined>();
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<Sheet | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listSheets({ page, page_size: pageSize, q: q || undefined, market, status });
      setRows(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, q, market, status]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load, reloadKey]);

  const openDetail = async (id: number) => setDetail(await getSheet(id));

  const onExport = async (s: Sheet, versionId: number, versionNo: number) => {
    const blob = await exportVersion(s.id, versionId);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `核价_${s.product_name}_v${versionNo}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns: ColumnsType<Sheet> = [
    {
      title: '产品',
      key: 'product',
      render: (_, s) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{s.product_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {[s.sku, s.asin].filter(Boolean).join(' · ') || '—'}
          </Typography.Text>
        </Space>
      ),
    },
    { title: '市场', dataIndex: 'markets', render: (ms: string[]) => (ms.length ? ms.join(' / ') : '—') },
    {
      title: '状态',
      key: 'status',
      render: (_, s) => (
        <Space size={4} wrap>
          <Tag>{s.status === 'saved' ? '已保存' : '草稿'}</Tag>
          {s.selections.length ? <Tag color="purple">已选定 {s.selections.length} 个市场</Tag> : null}
          {s.rules_updated ? <Tag color="orange">规则已更新</Tag> : null}
        </Space>
      ),
    },
    {
      title: '选定方案',
      key: 'sel',
      render: (_, s) =>
        s.selections.length ? s.selections.map((x) => `${x.market}: ${x.option_key}`).join('；') : '—',
    },
    { title: '更新时间', dataIndex: 'updated_at', render: (v: string) => v.replace('T', ' ').slice(0, 16) },
    {
      title: '操作',
      key: 'op',
      width: 260,
      render: (_, s) => (
        <Space size={0} wrap>
          <Button type="link" onClick={() => onOpen(s.id)}>
            {s.rules_updated ? '打开并重新核价' : '打开'}
          </Button>
          <Button type="link" onClick={() => openDetail(s.id).catch(() => undefined)}>
            版本
          </Button>
          <Button
            type="link"
            onClick={async () => {
              const c = await copySheet(s.id);
              message.success('已复制');
              await load();
              onOpen(c.id);
            }}
          >
            复制
          </Button>
          <Popconfirm
            title="删除这条核价记录？"
            description="会同时删除它的全部版本与选定记录，不可恢复。"
            onConfirm={async () => {
              await deleteSheet(s.id);
              message.success('已删除');
              await load();
            }}
          >
            <Button type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input.Search
          allowClear
          placeholder="产品名称 / SKU / ASIN"
          style={{ width: 260 }}
          onSearch={(v) => {
            setPage(1);
            setQ(v.trim());
          }}
        />
        <Select
          allowClear
          placeholder="市场"
          style={{ width: 120 }}
          value={market}
          onChange={(v) => {
            setPage(1);
            setMarket(v);
          }}
          options={meta.markets.map((m) => ({ value: m.code, label: `${m.name} ${m.code}` }))}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => {
            setPage(1);
            setStatus(v);
          }}
          options={[
            { value: 'draft', label: '草稿' },
            { value: 'saved', label: '已保存' },
          ]}
        />
      </Space>
      <Table<Sheet>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        locale={{ emptyText: '还没有核价记录' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />
      <Drawer
        width={560}
        open={!!detail}
        onClose={() => setDetail(null)}
        title={detail ? `${detail.product_name} · 版本` : ''}
      >
        {detail ? (
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={detail.versions}
            locale={{ emptyText: '还没有保存过版本（仅草稿）' }}
            columns={[
              {
                title: '版本',
                dataIndex: 'version_no',
                render: (n: number, v) => (
                  <Space size={4}>
                    v{n}
                    {v.id === detail.current_version_id ? <Tag color="blue">最新</Tag> : null}
                  </Space>
                ),
              },
              { title: '核价日期', dataIndex: 'calc_date' },
              { title: '模型', dataIndex: 'model_version' },
              {
                title: '操作',
                key: 'op',
                render: (_, v) => (
                  <Space size={0}>
                    <Button type="link" onClick={() => onOpen(detail.id, v.id)}>
                      查看
                    </Button>
                    <Button type="link" onClick={() => onExport(detail, v.id, v.version_no).catch(() => undefined)}>
                      导出
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        ) : null}
      </Drawer>
    </div>
  );
}
