import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Card, Radio, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';
import { type CheckSummary, listChecks, type TaskStatus } from '@/services/risk';
import { coverageText, dt, TASK_STATUS } from '../riskLabels';

/**
 * 核查记录：当前用户自己的外观专利核查单（后端按 owner 过滤，他人记录一律 404）。
 * 只读库，不触发任何智慧芽调用。
 */

type ReviewState = 'none' | 'partial' | 'complete';

export default function RecordsPage() {
  const [rows, setRows] = useState<CheckSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<TaskStatus | 'all'>('all');
  const [reviewState, setReviewState] = useState<ReviewState | 'all'>('all');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listChecks({
        page,
        page_size: pageSize,
        search_status: status === 'all' ? undefined : status,
        review_state: reviewState === 'all' ? undefined : reviewState,
      });
      setRows(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status, reviewState]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const columns: ColumnsType<CheckSummary> = [
    {
      title: '产品 / 版本',
      key: 'product',
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <Typography.Link onClick={() => history.push(`/ip/records/${r.id}`)}>{r.product_name}</Typography.Link>
          <Typography.Text type="secondary">{r.product_version}</Typography.Text>
        </Space>
      ),
    },
    { title: '销售市场', dataIndex: 'markets', render: (v: string[]) => v.join('、') },
    { title: '检索法域', dataIndex: 'jurisdictions', render: (v: string[]) => v.join('、') },
    {
      title: '检索状态',
      key: 'status',
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <Tag color={TASK_STATUS[r.search_status].color}>{TASK_STATUS[r.search_status].text}</Tag>
          {r.searched_at && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {coverageText(r.fetched_count, r.total_count)}
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '人工意见',
      key: 'review',
      render: (_, r) => {
        const n = r.reviewed_markets ?? 0;
        const all = r.markets.length;
        return <Tag color={n === 0 ? 'default' : n < all ? 'gold' : 'green'}>{`${n} / ${all} 个市场`}</Tag>;
      },
    },
    { title: '创建时间', dataIndex: 'created_at', render: (v: string) => dt(v) },
    { title: '最近检索', dataIndex: 'searched_at', render: (v: string | null) => dt(v) },
    {
      title: '操作',
      key: 'op',
      render: (_, r) => <Button size="small" onClick={() => history.push(`/ip/records/${r.id}`)}>详情</Button>,
    },
  ];

  return (
    <PageContainer
      title="核查记录"
      extra={<Button type="primary" onClick={() => history.push('/ip/patent')}>新建外观专利核查</Button>}
    >
      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <span>检索状态</span>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            options={[
              { label: '全部', value: 'all' },
              ...(Object.keys(TASK_STATUS) as TaskStatus[]).map((k) => ({ label: TASK_STATUS[k].text, value: k })),
            ]}
          />
          <span style={{ marginLeft: 16 }}>人工意见</span>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={reviewState}
            onChange={(e) => {
              setReviewState(e.target.value);
              setPage(1);
            }}
            options={[
              { label: '全部', value: 'all' },
              { label: '未审核', value: 'none' },
              { label: '部分市场', value: 'partial' },
              { label: '已全部审核', value: 'complete' },
            ]}
          />
        </Space>
        <Table<CheckSummary>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100],
            onChange: (p, s) => {
              setPage(p);
              setPageSize(s);
            },
          }}
        />
      </Card>
    </PageContainer>
  );
}
