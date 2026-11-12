import { PageContainer } from '@ant-design/pro-components';
import { useModel, useSearchParams } from '@umijs/max';
import { theme } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getIpMeta, type IpMeta, listWorkspaces, type Workspace } from '@/services/opportunityIp';
import DossierDetail from './Dossier/DossierDetail';
import DossierList from './Dossier/DossierList';
import NewDossier from './Dossier/NewDossier';
import ExpertsView from './ExpertsView';
import Conversation from './IpAgent/Conversation';
import type { DossierPreset } from './IpAgent/NewTaskComposer';
import NewTaskView from './NewTaskView';
import SchedulesView from './SchedulesView';
import Sidebar, { type View, type WsScope } from './Sidebar';

/**
 * 机会中心：机会档案 + 专家任务。
 *
 * - 仍是 `/opportunity` 单页、无二级路由；左栏是页面组件自带的（top-nav-structure 规格）。
 * - 视图状态全部在 URL 查询参数里，刷新可恢复：`dossier=<id>`、`task=<id>`、
 *   `view=new-dossier|new|experts|schedules`、`ws=<id>|none`、`group=<词库分组 id>`。
 * - 默认视图是机会档案列表（change add-opportunity-dossier，opportunity-center「机会档案是默认入口」）。
 *   档案团队可见；专家任务（目前只有 IP 专家）仍按创建人私有，用工作空间组织。
 * - 未接入的专家不发请求（opportunity-center 规格）。
 */

const PLAIN_VIEWS: View[] = ['new-dossier', 'new', 'experts', 'schedules'];

function parseScope(v: string | null): WsScope {
  if (v === 'none') return 'none';
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : 'all';
}

function positiveInt(v: string | null): number | null {
  const n = Number(v);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export default function OpportunityPage() {
  const { token } = theme.useToken();
  const { initialState } = useModel('@@initialState');
  const currentUserId = (initialState?.currentUser as { id?: number } | undefined)?.id ?? null;
  const [params, setParams] = useSearchParams();
  const taskId = positiveInt(params.get('task'));
  const dossierId = positiveInt(params.get('dossier'));
  const groupId = positiveInt(params.get('group'));
  const rawView = params.get('view') as View | null;
  const view: View = taskId ? 'task' : dossierId ? 'dossier' : rawView && PLAIN_VIEWS.includes(rawView) ? rawView : 'dossiers';
  const scope = parseScope(params.get('ws'));

  const [meta, setMeta] = useState<IpMeta | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [counts, setCounts] = useState({ total: 0, unassigned: 0 });
  const [version, setVersion] = useState(0);
  /** 从档案发起 IP 调研时带入的内容；只在本次会话里有效，刷新后按普通新建任务处理 */
  const [preset, setPreset] = useState<DossierPreset | null>(null);

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

  const navigate = (next: { view?: View; task?: number | null; dossier?: number | null; scope?: WsScope; group?: number | null }) => {
    const p = new URLSearchParams();
    const s = next.scope ?? scope;
    if (s !== 'all') p.set('ws', String(s));
    // 只切工作空间范围时保留当前视图；其余情况以 next 为准
    const onlyScope = next.view === undefined && next.task === undefined && next.dossier === undefined;
    const task = onlyScope ? taskId : (next.task ?? null);
    const dossier = onlyScope ? dossierId : (next.dossier ?? null);
    const v = onlyScope ? view : (next.view ?? (task ? 'task' : dossier ? 'dossier' : 'dossiers'));
    if (task) p.set('task', String(task));
    else if (dossier) p.set('dossier', String(dossier));
    else if (PLAIN_VIEWS.includes(v)) {
      p.set('view', v);
      if (v === 'new-dossier' && next.group) p.set('group', String(next.group));
    }
    if (v !== 'new') setPreset(null);
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
          {view === 'dossiers' && <DossierList onOpen={(id) => navigate({ dossier: id })} onNew={() => navigate({ view: 'new-dossier' })} />}
          {view === 'new-dossier' && (
            <NewDossier
              key={groupId ?? 'manual'}
              sites={meta?.markets ?? []}
              groupId={groupId}
              onCreated={(id) => navigate({ dossier: id })}
              onCancel={() => navigate({ view: 'dossiers' })}
            />
          )}
          {view === 'dossier' && dossierId && (
            <DossierDetail
              key={dossierId}
              id={dossierId}
              currentUserId={currentUserId}
              onBack={() => navigate({ view: 'dossiers' })}
              onChanged={bump}
              onOpenResearch={(id) => navigate({ task: id })}
              onStartResearch={(detail) => {
                setPreset({
                  dossierId: detail.dossier.id,
                  dossierTitle: detail.dossier.title,
                  product_name: detail.dossier.title,
                  description: detail.dossier.idea ?? undefined,
                  keywords: detail.keywords.map((k) => k.keyword).slice(0, 10),
                  markets: [detail.dossier.site],
                });
                // 先设 preset 再切视图：navigate 只在离开「新建任务」时清掉它
                const p = new URLSearchParams();
                p.set('view', 'new');
                setParams(p);
              }}
            />
          )}
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
              preset={preset}
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
