import { PageContainer } from '@ant-design/pro-components';
import { useSearchParams } from '@umijs/max';
import { theme } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getIpMeta, type IpMeta, listWorkspaces, type Workspace } from '@/services/opportunityIp';
import ExpertsView from './ExpertsView';
import Conversation from './IpAgent/Conversation';
import NewTaskView from './NewTaskView';
import SchedulesView from './SchedulesView';
import Sidebar, { type WsScope } from './Sidebar';

/**
 * 机会中心（change add-opportunity-ip-agent 决策 10，参考 WorkBuddy）：工作空间 + 任务对话。
 *
 * - 仍是 `/opportunity` 单页、无二级路由；左栏是页面组件自带的（top-nav-structure 规格）。
 * - 视图状态全部在 URL 查询参数里，刷新可恢复：`view=new|experts|schedules`、`task=<id>`、`ws=<id>|none`。
 * - 原来的阶段 Tab 都改成「专家」；只有 IP 专家接入，未接入的专家不发请求（opportunity-center 规格）。
 * - 进页面会请求 IP 专家的配置（`/api/opportunity/ip/meta`）与工作空间列表——两者都有真实数据支撑。
 */

type View = 'new' | 'experts' | 'schedules' | 'task';

function parseScope(v: string | null): WsScope {
  if (v === 'none') return 'none';
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : 'all';
}

export default function OpportunityPage() {
  const { token } = theme.useToken();
  const [params, setParams] = useSearchParams();
  const taskId = Number(params.get('task')) || null;
  const rawView = params.get('view');
  const view: View = taskId ? 'task' : rawView === 'experts' || rawView === 'schedules' ? rawView : 'new';
  const scope = parseScope(params.get('ws'));

  const [meta, setMeta] = useState<IpMeta | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [counts, setCounts] = useState({ total: 0, unassigned: 0 });
  const [version, setVersion] = useState(0);

  const loadWorkspaces = useCallback(async () => {
    const r = await listWorkspaces();
    setWorkspaces(r.items);
    setCounts({ total: r.total_count, unassigned: r.unassigned_count });
  }, []);

  useEffect(() => {
    getIpMeta().then(setMeta).catch(() => undefined);
  }, []);
  useEffect(() => {
    loadWorkspaces().catch(() => undefined);
  }, [loadWorkspaces, version]);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const navigate = (next: { view?: View; task?: number | null; scope?: WsScope }) => {
    const p = new URLSearchParams();
    const s = next.scope ?? scope;
    if (s !== 'all') p.set('ws', String(s));
    const task = next.task !== undefined ? next.task : next.view ? null : taskId;
    const v = next.view ?? (task ? 'task' : view);
    if (task) p.set('task', String(task));
    else if (v === 'experts' || v === 'schedules') p.set('view', v);
    setParams(p);
  };

  return (
    <PageContainer title={false} ghost>
      <div
        style={{
          display: 'flex',
          height: 'calc(100vh - 120px)',
          minHeight: 560,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: 12,
          overflow: 'hidden',
          background: token.colorBgContainer,
        }}
      >
        <Sidebar
          view={view}
          taskId={taskId}
          scope={scope}
          workspaces={workspaces}
          counts={counts}
          version={version}
          onNavigate={navigate}
          onWorkspacesChanged={bump}
        />
        <div style={{ flex: 1, minWidth: 0, minHeight: 0, background: token.colorBgLayout }}>
          {view === 'task' && taskId && meta && (
            <Conversation
              key={taskId}
              id={taskId}
              meta={meta}
              workspaces={workspaces}
              onChanged={bump}
              onBack={() => navigate({ view: 'new' })}
            />
          )}
          {view === 'new' && (
            <NewTaskView
              meta={meta}
              workspaces={workspaces}
              defaultWorkspaceId={typeof scope === 'number' ? scope : null}
              onCreated={(id) => {
                bump();
                navigate({ task: id });
              }}
            />
          )}
          {view === 'experts' && <ExpertsView onStart={() => navigate({ view: 'new' })} />}
          {view === 'schedules' && <SchedulesView onOpenTask={(id) => navigate({ task: id })} onChanged={bump} />}
        </div>
      </div>
    </PageContainer>
  );
}
