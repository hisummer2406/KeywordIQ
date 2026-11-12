import {
  AppstoreOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  RiseOutlined,
  InboxOutlined,
  MoreOutlined,
  PlusOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { Badge, Button, Dropdown, Input, Modal, Spin, Tooltip, Typography, theme } from 'antd';
import { type CSSProperties, type ReactNode, useCallback, useEffect, useState } from 'react';
import { TASK_STATUS } from '@/pages/IP/riskLabels';
import {
  createWorkspace,
  deleteWorkspace,
  listResearches,
  renameWorkspace,
  type ResearchSummary,
  type Workspace,
} from '@/services/opportunityIp';
import { TASK_KIND } from './IpAgent/ipLabels';

/**
 * 机会中心页内左栏（change add-opportunity-ip-agent 决策 10、11）：机会档案、专家、定时任务、
 * 个人的工作空间与其下的专家任务。它是页面组件的一部分，不是布局层的二级菜单（top-nav-structure）。
 */

/** 工作空间范围：全部、未归类，或某个工作空间 id */
export type WsScope = 'all' | 'none' | number;

/** 主区视图。`dossiers` 是默认视图（机会档案列表）。 */
export type View = 'dossiers' | 'dossier' | 'new-dossier' | 'leads' | 'new' | 'experts' | 'schedules' | 'task';

const PAGE_SIZE = 30;

interface Props {
  view: View;
  taskId: number | null;
  scope: WsScope;
  workspaces: Workspace[];
  counts: { total: number; unassigned: number };
  /** 任务或工作空间变了就加一，左栏重新取列表 */
  version: number;
  onNavigate: (next: { view?: View; task?: number | null; dossier?: number | null; scope?: WsScope }) => void;
  onWorkspacesChanged: () => void;
}

function Row({
  active,
  icon,
  label,
  extra,
  onClick,
  style,
}: {
  active?: boolean;
  icon?: ReactNode;
  label: ReactNode;
  extra?: ReactNode;
  onClick: () => void;
  style?: CSSProperties;
}) {
  const { token } = theme.useToken();
  const [hover, setHover] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 10px',
        borderRadius: 8,
        cursor: 'pointer',
        background: active ? token.colorPrimaryBg : hover ? token.colorFillTertiary : undefined,
        color: active ? token.colorPrimary : undefined,
        ...style,
      }}
    >
      {icon}
      <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</div>
      {extra}
    </div>
  );
}

function SectionTitle({ children, extra }: { children: ReactNode; extra?: ReactNode }) {
  const { token } = theme.useToken();
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '14px 10px 4px', color: token.colorTextTertiary, fontSize: 12 }}>
      <span style={{ flex: 1 }}>{children}</span>
      {extra}
    </div>
  );
}

const STATUS_DOT: Record<string, 'processing' | 'success' | 'error' | 'warning' | 'default'> = {
  queued: 'processing',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  unknown: 'warning',
};

export default function Sidebar({ view, taskId, scope, workspaces, counts, version, onNavigate, onWorkspacesChanged }: Props) {
  const { token } = theme.useToken();
  const [tasks, setTasks] = useState<ResearchSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<{ id: number | null; name: string } | null>(null);

  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        const r = await listResearches({
          page: p,
          page_size: PAGE_SIZE,
          ...(scope === 'none' ? { unassigned: true } : typeof scope === 'number' ? { workspace_id: scope } : {}),
        });
        setTasks((prev) => (p === 1 ? r.items : [...prev, ...r.items]));
        setTotal(r.total);
        setPage(p);
      } finally {
        setLoading(false);
      }
    },
    [scope],
  );

  useEffect(() => {
    load(1).catch(() => undefined);
  }, [load, version]);

  const saveWorkspace = async () => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!name) return;
    if (editing.id === null) {
      const ws = await createWorkspace(name);
      onNavigate({ scope: ws.id });
    } else {
      await renameWorkspace(editing.id, name);
    }
    setEditing(null);
    onWorkspacesChanged();
  };

  const removeWorkspace = (ws: Workspace) => {
    Modal.confirm({
      title: `删除工作空间「${ws.name}」？`,
      content: ws.task_count ? '里面还有任务，需要先把任务移到别的工作空间。' : '只删除这个分组，不影响任何任务。',
      okText: '删除',
      okButtonProps: { danger: true, disabled: ws.task_count > 0 },
      onOk: async () => {
        await deleteWorkspace(ws.id);
        if (scope === ws.id) onNavigate({ scope: 'all' });
        onWorkspacesChanged();
      },
    });
  };

  const scopeName =
    scope === 'all' ? '全部任务' : scope === 'none' ? '未归类' : (workspaces.find((w) => w.id === scope)?.name ?? '工作空间');

  return (
    <div
      style={{
        width: 260,
        flex: 'none',
        borderRight: `1px solid ${token.colorBorderSecondary}`,
        display: 'flex',
        flexDirection: 'column',
        background: token.colorBgContainer,
        minHeight: 0,
      }}
    >
      <div style={{ padding: 12 }}>
        <Button type="primary" block icon={<PlusOutlined />} onClick={() => onNavigate({ view: 'new-dossier' })}>
          新建档案
        </Button>
      </div>
      <div style={{ padding: '0 8px' }}>
        <Row
          active={view === 'dossiers' || view === 'dossier' || view === 'new-dossier'}
          icon={<FolderOpenOutlined />}
          label="机会档案"
          onClick={() => onNavigate({ view: 'dossiers' })}
        />
        <Row active={view === 'leads'} icon={<RiseOutlined />} label="线索" onClick={() => onNavigate({ view: 'leads' })} />
        <Row active={view === 'experts'} icon={<AppstoreOutlined />} label="专家" onClick={() => onNavigate({ view: 'experts', task: null })} />
        <Row
          active={view === 'schedules'}
          icon={<ClockCircleOutlined />}
          label="定时任务"
          onClick={() => onNavigate({ view: 'schedules', task: null })}
        />
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px', minHeight: 0 }}>
        <SectionTitle
          extra={
            <Tooltip title="向专家发起任务">
              <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => onNavigate({ view: 'new', task: null })} />
            </Tooltip>
          }
        >
          专家任务（仅自己可见）
        </SectionTitle>
        <SectionTitle
          extra={
            <Tooltip title="新建工作空间">
              <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => setEditing({ id: null, name: '' })} />
            </Tooltip>
          }
        >
          工作空间
        </SectionTitle>
        <Row
          active={scope === 'all'}
          icon={<UnorderedListOutlined />}
          label="全部任务"
          extra={<Typography.Text type="secondary">{counts.total}</Typography.Text>}
          onClick={() => onNavigate({ scope: 'all' })}
        />
        <Row
          active={scope === 'none'}
          icon={<InboxOutlined />}
          label="未归类"
          extra={<Typography.Text type="secondary">{counts.unassigned}</Typography.Text>}
          onClick={() => onNavigate({ scope: 'none' })}
        />
        {workspaces.map((ws) => (
          <Row
            key={ws.id}
            active={scope === ws.id}
            icon={<FolderOutlined />}
            label={ws.name}
            onClick={() => onNavigate({ scope: ws.id })}
            extra={
              <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} role="presentation">
                <Typography.Text type="secondary" style={{ marginRight: 4 }}>
                  {ws.task_count}
                </Typography.Text>
                <Dropdown
                  trigger={['click']}
                  menu={{
                    items: [
                      { key: 'rename', icon: <EditOutlined />, label: '重命名', onClick: () => setEditing({ id: ws.id, name: ws.name }) },
                      { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true, onClick: () => removeWorkspace(ws) },
                    ],
                  }}
                >
                  <Button type="text" size="small" icon={<MoreOutlined />} />
                </Dropdown>
              </span>
            }
          />
        ))}

        <SectionTitle>{`${scopeName} · ${total}`}</SectionTitle>
        {tasks.map((t) => (
          <Row
            key={t.id}
            active={view === 'task' && taskId === t.id}
            style={{ alignItems: 'flex-start' }}
            label={
              <div>
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.product_name}</div>
                <div style={{ fontSize: 12, color: token.colorTextTertiary, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {t.last_task ? (
                    <Badge
                      status={STATUS_DOT[t.last_task.status]}
                      text={
                        <span style={{ fontSize: 12, color: token.colorTextTertiary }}>
                          {`${TASK_KIND[t.last_task.kind]} ${TASK_STATUS[t.last_task.status].text}`}
                        </span>
                      }
                    />
                  ) : (
                    '尚未开始'
                  )}
                  {` · 候选 ${t.candidate_count}`}
                </div>
              </div>
            }
            extra={
              t.schedule_enabled !== null && (
                <Tooltip title={t.schedule_enabled ? '定时复检已启用' : '定时复检已暂停'}>
                  <ClockCircleOutlined style={{ color: t.schedule_enabled ? token.colorPrimary : token.colorTextQuaternary, marginTop: 4 }} />
                </Tooltip>
              )
            }
            onClick={() => onNavigate({ view: 'task', task: t.id })}
          />
        ))}
        {loading && <Spin size="small" style={{ display: 'block', margin: '8px auto' }} />}
        {!loading && tasks.length === 0 && (
          <Typography.Text type="secondary" style={{ display: 'block', padding: '4px 10px', fontSize: 12 }}>
            这里还没有任务
          </Typography.Text>
        )}
        {!loading && tasks.length < total && (
          <Button type="link" size="small" block onClick={() => load(page + 1).catch(() => undefined)}>
            加载更多
          </Button>
        )}
      </div>

      <Modal
        open={editing !== null}
        title={editing?.id === null ? '新建工作空间' : '重命名工作空间'}
        okText="保存"
        onOk={() => saveWorkspace().catch(() => undefined)}
        onCancel={() => setEditing(null)}
        okButtonProps={{ disabled: !editing?.name.trim() }}
        destroyOnClose
      >
        <Input
          autoFocus
          maxLength={50}
          placeholder="例如：户外水杯、宠物用品"
          value={editing?.name ?? ''}
          onChange={(e) => setEditing((s) => (s ? { ...s, name: e.target.value } : s))}
          onPressEnter={() => saveWorkspace().catch(() => undefined)}
        />
      </Modal>
    </div>
  );
}
