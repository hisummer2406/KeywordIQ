import { Button, Popconfirm, Space, Switch, Table, Tag, Typography } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { dt } from '@/pages/IP/riskLabels';
import { deleteSchedule, type IpSchedule, listSchedules, saveSchedule } from '@/services/opportunityIp';
import { scheduleText } from './IpAgent/ipLabels';

/**
 * 定时任务（change add-opportunity-ip-agent 决策 13）。在任务对话里创建；这里集中查看、暂停与删除。
 */

type Row = IpSchedule & { product_name: string; workspace_id: number | null };

export default function SchedulesView({ onOpenTask, onChanged }: { onOpenTask: (id: number) => void; onChanged: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows((await listSchedules()).items);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const toggle = async (r: Row, enabled: boolean) => {
    await saveSchedule(r.research_id, {
      frequency: r.frequency,
      weekday: r.weekday,
      day_of_month: r.day_of_month,
      hour: r.hour,
      queries: r.queries,
      enabled,
    });
    await load();
    onChanged();
  };

  return (
    <div style={{ padding: 24, overflowY: 'auto', height: '100%' }}>
      <div style={{ maxWidth: 1080, margin: '0 auto' }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          定时任务
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          定时复检会在设定时间用你执行过的检索式重新检索，新出现的专利追加到任务的候选里。每次执行都计入 MCP 预算；
          预算不足或任务里有别的操作在进行时，这一次会跳过并记录原因，不会补跑。在任务对话里点「定时复检」创建。
        </Typography.Paragraph>
        <Table<Row>
          rowKey="id"
          loading={loading}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: '还没有定时任务' }}
          columns={[
            {
              title: '任务',
              dataIndex: 'product_name',
              render: (v: string, r) => <Typography.Link onClick={() => onOpenTask(r.research_id)}>{v}</Typography.Link>,
            },
            { title: '频率', width: 150, render: (_: unknown, r) => scheduleText(r) },
            { title: '检索式', width: 80, render: (_: unknown, r) => `${r.queries.length} 条` },
            { title: '下次执行', width: 160, render: (_: unknown, r) => (r.enabled ? dt(r.next_run_at) : '已暂停') },
            {
              title: '最近一次',
              render: (_: unknown, r) =>
                !r.last_run_at ? (
                  <Typography.Text type="secondary">还没执行过</Typography.Text>
                ) : (
                  <Space size={4} wrap>
                    <Typography.Text type="secondary">{dt(r.last_run_at)}</Typography.Text>
                    {r.last_status === 'skipped' ? (
                      <Tag color="warning">{`已跳过：${r.last_reason ?? ''}`}</Tag>
                    ) : (
                      <Tag color="processing">已入队检索</Tag>
                    )}
                  </Space>
                ),
            },
            {
              title: '启用',
              width: 70,
              render: (_: unknown, r) => <Switch size="small" checked={r.enabled} onChange={(v) => toggle(r, v).catch(() => undefined)} />,
            },
            {
              title: '',
              width: 70,
              render: (_: unknown, r) => (
                <Popconfirm
                  title="删除这个定时复检？"
                  okText="删除"
                  onConfirm={async () => {
                    await deleteSchedule(r.research_id);
                    await load();
                    onChanged();
                  }}
                >
                  <Button type="link" danger size="small">
                    删除
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </div>
    </div>
  );
}
