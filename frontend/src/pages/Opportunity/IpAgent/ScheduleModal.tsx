import { Alert, Button, Checkbox, Form, InputNumber, Modal, Popconfirm, Radio, Select, Space, Switch, Typography } from 'antd';
import { useState } from 'react';
import { dt } from '@/pages/IP/riskLabels';
import { deleteSchedule, type ResearchDetail, saveSchedule } from '@/services/opportunityIp';
import { WEEKDAY_OPTIONS } from './ipLabels';
import { executedQueries } from './timeline';

/**
 * 定时复检（决策 13）。只能从本调研**已成功执行过**的检索式里选——定时任务执行的仍是你确认过、
 * 语法已验证的检索式。每次执行仍受 MCP 预算约束；预算不足或有任务在执行时本次跳过并记录原因。
 */

interface Props {
  open: boolean;
  data: ResearchDetail;
  onClose: () => void;
  onSaved: () => void;
}

interface Values {
  frequency: 'weekly' | 'monthly';
  weekday: number;
  day_of_month: number;
  hour: number;
  queries: string[];
  enabled: boolean;
}

const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: h, label: `${String(h).padStart(2, '0')}:00` }));

export default function ScheduleModal({ open, data, onClose, onSaved }: Props) {
  const [form] = Form.useForm<Values>();
  const [saving, setSaving] = useState(false);
  const options = executedQueries(data);
  const limitOf = new Map(options.map((q) => [q.query_text, q.limit]));
  const s = data.schedule;
  const frequency = Form.useWatch('frequency', form) ?? 'weekly';
  const picked = Form.useWatch('queries', form) ?? [];

  // 弹窗 destroyOnClose，每次打开表单重新挂载，初始值取当时的定时任务
  const initialValues: Values = {
    frequency: s?.frequency ?? 'weekly',
    weekday: s?.weekday ?? 0,
    day_of_month: s?.day_of_month ?? 1,
    hour: s?.hour ?? 9,
    queries: s ? s.queries.map((q) => q.query_text).filter((q) => limitOf.has(q)) : options.slice(0, 3).map((q) => q.query_text),
    enabled: s?.enabled ?? true,
  };

  const submit = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      await saveSchedule(data.id, {
        frequency: v.frequency,
        weekday: v.frequency === 'weekly' ? v.weekday : null,
        day_of_month: v.frequency === 'monthly' ? v.day_of_month : null,
        hour: v.hour,
        queries: v.queries.map((q) => ({ query_text: q, limit: limitOf.get(q) ?? 20 })),
        enabled: v.enabled,
      });
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    await deleteSchedule(data.id);
    onSaved();
    onClose();
  };

  return (
    <Modal
      open={open}
      title="定时复检"
      okText="保存"
      onOk={submit}
      confirmLoading={saving}
      onCancel={onClose}
      okButtonProps={{ disabled: !options.length }}
      destroyOnClose
      footer={(orig) => (
        <Space>
          {s && (
            <Popconfirm title="删除这个定时复检？" okText="删除" onConfirm={remove}>
              <Button danger>删除</Button>
            </Popconfirm>
          )}
          {orig}
        </Space>
      )}
    >
      <Typography.Paragraph type="secondary">
        到点后按所选检索式重新检索，新出现的专利会追加到候选里。每次执行都会消耗 MCP 预算（本调研上限{' '}
        {data.budget.per_research} 次，已用 {data.budget.used} 次）；预算不足或有任务在执行时，本次跳过并记录原因，不会补跑。
      </Typography.Paragraph>
      {s && (
        <Typography.Paragraph type="secondary">
          {`下次执行：${dt(s.next_run_at)}`}
          {s.last_run_at && `；最近一次 ${dt(s.last_run_at)} ${s.last_status === 'skipped' ? `已跳过（${s.last_reason ?? ''}）` : '已入队'}`}
        </Typography.Paragraph>
      )}
      {!options.length ? (
        <Alert type="info" showIcon message="还没有成功执行过的检索式。先在对话里执行一次检索，再设定时复检。" />
      ) : (
        <Form<Values> form={form} layout="vertical" initialValues={initialValues} preserve={false}>
          <Form.Item name="frequency" label="频率">
            <Radio.Group optionType="button" buttonStyle="solid" options={[{ value: 'weekly', label: '每周' }, { value: 'monthly', label: '每月' }]} />
          </Form.Item>
          <Space size={12} align="start">
            {frequency === 'weekly' ? (
              <Form.Item name="weekday" label="周几">
                <Select style={{ width: 120 }} options={WEEKDAY_OPTIONS} />
              </Form.Item>
            ) : (
              <Form.Item name="day_of_month" label="日期" extra="1–28 日，避免小月没有这一天">
                <InputNumber min={1} max={28} precision={0} addonAfter="日" />
              </Form.Item>
            )}
            <Form.Item name="hour" label="时刻（北京时间）">
              <Select style={{ width: 120 }} options={HOURS} />
            </Form.Item>
          </Space>
          <Form.Item
            name="queries"
            label={`复检的检索式（${picked.length} 条，每次调用 ${picked.length} 次 MCP）`}
            rules={[
              { required: true, message: '至少选一条' },
              { validator: async (_, v?: string[]) => { if ((v ?? []).length > 3) throw new Error('最多 3 条'); } },
            ]}
          >
            <Checkbox.Group style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {options.map((q) => (
                <Checkbox key={q.query_text} value={q.query_text}>
                  <Typography.Text code>{q.query_text}</Typography.Text>
                  <Typography.Text type="secondary">{` 取前 ${q.limit} 条`}</Typography.Text>
                </Checkbox>
              ))}
            </Checkbox.Group>
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}
