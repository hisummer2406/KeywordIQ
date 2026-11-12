import { Alert, Button, Input, Modal, Popconfirm, Radio, Select, Space, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useState } from 'react';
import {
  activateRule,
  copyRule,
  deleteRule,
  endRule,
  listRules,
  retireRule,
  type PricingMeta,
  type Rule,
  type RuleKind,
  type RuleStatus,
} from '@/services/pricing';
import RuleEditor from './RuleEditor';

/**
 * 费用配置：共享规则的版本列表与启停。
 *
 * 所有人可读（核价明细本来就展示所用规则）；维护按钮只对 `pricing:manage-rates` 显示，
 * 后端逐端点再校验一次。启用时后端检查同范围生效期重叠，重叠直接 409 拒绝。
 */

const STATUS_TAG: Record<RuleStatus, { text: string; color: string }> = {
  draft: { text: '草稿', color: 'default' },
  active: { text: '已启用', color: 'green' },
  retired: { text: '已停用', color: 'red' },
};

interface Props {
  meta: PricingMeta;
  canManage: boolean;
  onChanged?: () => void;
}

export default function RatesManager({ meta, canManage, onChanged }: Props) {
  const [kind, setKind] = useState<RuleKind>('market_params');
  const [status, setStatus] = useState<RuleStatus | undefined>();
  const [rows, setRows] = useState<Rule[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<{ rule: Rule | null; readOnly: boolean } | null>(null);
  const [ending, setEnding] = useState<Rule | null>(null);
  const [endDate, setEndDate] = useState('');
  const [retiring, setRetiring] = useState<Rule | null>(null);
  const [reason, setReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listRules({ kind, status, page, page_size: 50 });
      setRows(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }, [kind, status, page]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const changed = async (msg: string) => {
    message.success(msg);
    await load();
    onChanged?.();
  };

  const columns: ColumnsType<Rule> = [
    { title: '范围', dataIndex: 'scope_key', width: 150 },
    { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => `v${v}` },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: RuleStatus) => <Tag color={STATUS_TAG[s].color}>{STATUS_TAG[s].text}</Tag>,
    },
    {
      title: '生效期',
      key: 'period',
      width: 200,
      render: (_, r) => `${r.effective_from} ～ ${r.effective_to ?? '长期'}`,
    },
    {
      title: '来源',
      dataIndex: 'source',
      ellipsis: { showTitle: false },
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          {v}
        </Tooltip>
      ),
    },
    {
      title: '审核说明',
      dataIndex: 'review_note',
      ellipsis: { showTitle: false },
      render: (v: string | null, r) => (
        <Tooltip title={r.retired_reason ? `停用原因：${r.retired_reason}` : v} placement="topLeft">
          {r.retired_reason ? `停用：${r.retired_reason}` : v ?? '—'}
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 300,
      render: (_, r) => (
        <Space size={0} wrap>
          <Button type="link" onClick={() => setEditing({ rule: r, readOnly: !canManage || r.status !== 'draft' })}>
            {canManage && r.status === 'draft' ? '编辑' : '查看'}
          </Button>
          {canManage && r.status === 'draft' ? (
            <>
              <Popconfirm
                title="启用这条规则？"
                description="启用后不能再修改；同范围生效期重叠会被拒绝。请确认已对照来源核对数值。"
                onConfirm={async () => {
                  await activateRule(r.id, r.lock_version);
                  await changed('已启用');
                }}
              >
                <Button type="link">启用</Button>
              </Popconfirm>
              <Popconfirm
                title="删除这条草稿？"
                onConfirm={async () => {
                  await deleteRule(r.id, r.lock_version);
                  await changed('已删除');
                }}
              >
                <Button type="link" danger>
                  删除
                </Button>
              </Popconfirm>
            </>
          ) : null}
          {canManage && r.status === 'active' ? (
            <>
              <Button type="link" onClick={() => setEnding(r)}>
                设定失效日
              </Button>
              <Button type="link" danger onClick={() => setRetiring(r)}>
                停用
              </Button>
            </>
          ) : null}
          {canManage && r.status !== 'draft' ? (
            <Button
              type="link"
              onClick={async () => {
                const c = await copyRule(r.id);
                await changed(`已复制为草稿 v${c.version}`);
                setEditing({ rule: c, readOnly: false });
              }}
            >
              复制为新草稿
            </Button>
          ) : null}
        </Space>
      ),
    },
  ];

  const unconfigured = meta.markets.filter((m) => m.missing.length);
  const kindName = Object.fromEntries(meta.rule_kinds.map((k) => [k.key, k.name]));

  return (
    <div>
      {unconfigured.length ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title="以下市场缺少已启用的必需规则，暂时不能核价"
          description={unconfigured
            .map((m) => `${m.name} ${m.code}：${m.missing.map((k) => kindName[k] ?? k).join('、')}`)
            .join('；')}
        />
      ) : null}
      <Space style={{ marginBottom: 12 }} wrap>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={kind}
          onChange={(e) => {
            setPage(1);
            setKind(e.target.value as RuleKind);
          }}
          options={meta.rule_kinds.map((k) => ({ label: k.name, value: k.key }))}
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
          options={(Object.keys(STATUS_TAG) as RuleStatus[]).map((s) => ({ value: s, label: STATUS_TAG[s].text }))}
        />
        {canManage ? (
          <Button type="primary" onClick={() => setEditing({ rule: null, readOnly: false })}>
            新建草稿
          </Button>
        ) : null}
      </Space>
      <Table<Rule>
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={rows}
        locale={{ emptyText: '这一类还没有规则' }}
        pagination={{ current: page, pageSize: 50, total, onChange: setPage }}
        scroll={{ x: 1100 }}
      />

      <RuleEditor
        open={!!editing}
        meta={meta}
        rule={editing?.rule ?? null}
        kind={kind}
        readOnly={editing?.readOnly ?? true}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          await changed('草稿已保存');
        }}
      />
      <Modal
        title={ending ? `设定失效日：${ending.scope_key} v${ending.version}` : ''}
        open={!!ending}
        onCancel={() => setEnding(null)}
        onOk={async () => {
          if (!ending || !endDate) return;
          await endRule(ending.id, ending.lock_version, endDate);
          setEnding(null);
          setEndDate('');
          await changed('已设定失效日');
        }}
      >
        <p>为新版本让出生效期时使用：旧版本在此日（含）之后不再命中。</p>
        <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
      </Modal>
      <Modal
        title={retiring ? `停用：${retiring.scope_key} v${retiring.version}` : ''}
        open={!!retiring}
        okButtonProps={{ danger: true, disabled: !reason.trim() }}
        okText="停用"
        onCancel={() => setRetiring(null)}
        onOk={async () => {
          if (!retiring) return;
          await retireRule(retiring.id, retiring.lock_version, reason.trim());
          setRetiring(null);
          setReason('');
          await changed('已停用');
        }}
      >
        <p>停用后不再参与核价，历史记录保留原金额并提示重新核价。</p>
        <Input.TextArea rows={3} maxLength={500} placeholder="停用原因（必填）" value={reason}
          onChange={(e) => setReason(e.target.value)} />
      </Modal>
    </div>
  );
}
