import {
  CheckOutlined,
  ClockCircleOutlined,
  DownOutlined,
  EditOutlined,
  EllipsisOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  ProfileOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { Bubble, type BubbleItemType, Sender } from '@ant-design/x';
import {
  Alert,
  Button,
  Dropdown,
  type MenuProps,
  Result,
  Space,
  Spin,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  askResearch,
  getResearch,
  type IpMeta,
  moveResearch,
  requestAnalysis,
  requestPlan,
  type ResearchDetail,
  type Workspace,
} from '@/services/opportunityIp';
import { dt } from '@/pages/IP/riskLabels';
import { newToken } from '@/services/risk';
import { BubbleTitle, MetaLine, useChatRoles } from '../chat/roles';
import { EXPERT_BY_KEY } from '../experts';
import { scheduleText, TASK_KIND } from './ipLabels';
import MessageBody, { bubbleMeta } from './MessageItems';
import { type ActionKey, nextAction } from './nextAction';
import QueryComposer from './QueryComposer';
import ResultsDrawer, { type ResultsSection } from './ResultsDrawer';
import ScheduleModal from './ScheduleModal';
import { buildTimeline } from './timeline';

/**
 * IP 专家任务对话（change add-opportunity-ip-agent 决策 10、12；change refine-opportunity-chat-ui 决策 D3、D4）。
 *
 * - 消息流由已存事实拼出（timeline.ts）；有在途任务时每 3 秒轮询详情（只读库，不产生外部调用），没有就停。
 * - 底部输入框发送的是**追问**（只调用大模型）；检索与取证必须在卡片里确认调用次数后才入队。
 * - 输入区按任务进度只突出一个主要动作（nextAction），其余动作在「更多」里，不可用时写明原因。
 */

const POLL_MS = 3000;
/** 追问最长字数（与原输入框 maxLength 一致） */
const ASK_MAX = 2000;
const IP = EXPERT_BY_KEY.ip;
const BUSY = '等当前任务完成';

interface Props {
  id: number;
  meta: IpMeta;
  workspaces: Workspace[];
  /** 任务状态或所属工作空间变化后，通知左栏刷新 */
  onChanged: () => void;
  onBack: () => void;
}

interface ActionDef {
  label: string;
  icon: ReactNode;
  /** 可用时的说明（调用什么、会不会计费） */
  tip?: string;
  /** 非空即不可用，并说明原因 */
  disabledReason?: string;
  loading?: boolean;
  onClick: () => void;
}

/** 「更多」里的顺序；确认检索式只作为主要动作出现（方案卡里也有入口） */
const MORE_ORDER: ActionKey[] = ['plan', 'manual', 'analysis', 'review'];

export default function Conversation({ id, meta, workspaces, onChanged, onBack }: Props) {
  const { token } = theme.useToken();
  const roles = useChatRoles('ip');
  const [data, setData] = useState<ResearchDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [results, setResults] = useState<{ open: boolean; section: ResultsSection }>({
    open: false,
    section: 'candidates',
  });
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [text, setText] = useState('');
  const [acting, setActing] = useState<string | null>(null);
  const tokens = useRef({ plan: newToken(), analysis: newToken(), ask: newToken() });

  const load = useCallback(async () => {
    try {
      setData(await getResearch(id));
    } catch (e: any) {
      if (e?.response?.status === 404) setMissing(true);
      throw e;
    }
  }, [id]);

  useEffect(() => {
    setData(null);
    setMissing(false);
    setDraft(null);
    setText('');
    load().catch(() => undefined);
  }, [load]);

  const active = data?.active_task;
  const wasActive = useRef(false);
  useEffect(() => {
    if (!active) {
      // 任务刚结束：左栏的状态标签也要更新
      if (wasActive.current) onChanged();
      wasActive.current = false;
      return undefined;
    }
    wasActive.current = true;
    const t = setInterval(() => load().catch(() => undefined), POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, load]);

  // 新方案到达、且还没执行过任何检索时，把方案填进待确认卡片（不会自动执行）
  const latestPlan = data?.plans[0];
  useEffect(() => {
    if (latestPlan && data && data.queries.length === 0)
      setDraft(latestPlan.content.queries.map((q) => q.query_text));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestPlan?.id]);

  const timeline = useMemo(() => (data ? buildTimeline(data) : []), [data]);

  if (missing) {
    return (
      <Result status="404" title="任务不存在" extra={<Button onClick={onBack}>返回</Button>} />
    );
  }
  if (!data) return <Spin style={{ display: 'block', margin: '96px auto' }} />;

  const busy = !!active;
  const refresh = () => {
    load().catch(() => undefined);
    onChanged();
  };
  const act = async (name: string, fn: () => Promise<unknown>) => {
    setActing(name);
    try {
      await fn();
      refresh();
    } catch {
      // 错误提示由全局请求错误处理给出（预算不足、在途任务等）；这里只避免未处理的 Promise 拒绝
    } finally {
      setActing(null);
    }
  };
  const plan = () =>
    act('plan', async () => {
      await requestPlan(data.id, tokens.current.plan);
      tokens.current.plan = newToken();
    });
  const analyze = () =>
    act('analysis', async () => {
      await requestAnalysis(data.id, tokens.current.analysis);
      tokens.current.analysis = newToken();
    });
  const send = () => {
    const content = text.trim();
    if (!content || busy) return;
    act('ask', async () => {
      await askResearch(data.id, { client_token: tokens.current.ask, content });
      tokens.current.ask = newToken();
      setText('');
    });
  };
  const openResults = (section: ResultsSection) => setResults({ open: true, section });

  const llmTip = data.llm_configured ? '' : '大模型未配置';
  const draftOpen = draft !== null;
  const draftReason = busy ? BUSY : draftOpen ? '待确认的检索式已打开' : '';
  const actions: Record<ActionKey, ActionDef> = {
    plan: {
      label: data.plans.length ? '重新生成检索方案' : '生成检索方案',
      icon: <ThunderboltOutlined />,
      tip: '调用一次大模型，根据产品信息写检索式建议（不调用智慧芽）',
      disabledReason: llmTip || (busy ? BUSY : ''),
      loading: acting === 'plan',
      onClick: plan,
    },
    confirmPlan: {
      label: '确认检索式',
      icon: <CheckOutlined />,
      tip: '用最新方案打开待确认的检索式；确认调用次数后才会执行',
      disabledReason: draftReason,
      onClick: () => latestPlan && setDraft(latestPlan.content.queries.map((q) => q.query_text)),
    },
    manual: {
      label: '手写检索式',
      icon: <EditOutlined />,
      tip: '自己写检索式；确认调用次数后才会执行',
      disabledReason: draftReason,
      onClick: () => setDraft([]),
    },
    analysis: {
      label: data.analyses.length ? '重新生成风险分析' : '生成风险分析',
      icon: <FileSearchOutlined />,
      tip: '调用一次大模型，基于已取得的证据写风险分析',
      disabledReason: llmTip || (!data.search_done ? '先成功执行至少一条检索' : busy ? BUSY : ''),
      loading: acting === 'analysis',
      onClick: analyze,
    },
    review: {
      label: '签署人工意见',
      icon: <SafetyCertificateOutlined />,
      tip: '按销售市场签署，只追加不修改',
      onClick: () => openResults('reviews'),
    },
  };
  const primaryKey = nextAction(data);
  // 待确认卡已打开时它就是焦点，不再重复一个主按钮
  const primary =
    primaryKey && !(draftOpen && (primaryKey === 'confirmPlan' || primaryKey === 'manual'))
      ? actions[primaryKey]
      : null;
  const moreItems: MenuProps['items'] = MORE_ORDER.filter((k) => k !== primaryKey).map((k) => {
    const a = actions[k];
    return {
      key: k,
      icon: a.icon,
      disabled: !!a.disabledReason,
      label: a.disabledReason ? `${a.label}（${a.disabledReason}）` : a.label,
    };
  });

  const workspaceName = workspaces.find((w) => w.id === data.workspace_id)?.name ?? '未归类';
  const scheduleLabel = data.schedule
    ? `${scheduleText(data.schedule)}${data.schedule.enabled ? '' : '（已暂停）'}`
    : null;
  const taskMenu: MenuProps['items'] = [
    {
      key: 'schedule',
      icon: <ClockCircleOutlined />,
      label: data.schedule
        ? `定时复检：${scheduleLabel} · 下次 ${dt(data.schedule.next_run_at)}`
        : '设置定时复检',
    },
    {
      key: 'move',
      icon: <FolderOpenOutlined />,
      label: '移动到工作空间',
      children: [{ id: 0, name: '未归类' }, ...workspaces].map((w) => ({
        key: `ws-${w.id}`,
        label: w.name,
        icon: (data.workspace_id ?? 0) === w.id ? <CheckOutlined /> : undefined,
        disabled: (data.workspace_id ?? 0) === w.id,
      })),
    },
    { key: 'tasks', icon: <UnorderedListOutlined />, label: `任务与调用记录（${data.tasks.length}）` },
  ];
  const onTaskMenu: MenuProps['onClick'] = ({ key }) => {
    if (key === 'schedule') setScheduleOpen(true);
    else if (key === 'tasks') openResults('tasks');
    else if (key.startsWith('ws-')) {
      const ws = Number(key.slice(3));
      act('move', () => moveResearch(data.id, ws || null));
    }
  };

  const latestPlanId = latestPlan?.id;
  const bubbleItems: BubbleItemType[] = timeline.map((item) => {
    const m = bubbleMeta(item);
    return {
      key: item.key,
      role: m.role,
      header: m.title ? <BubbleTitle>{m.title}</BubbleTitle> : undefined,
      footer: m.meta ? <MetaLine parts={m.meta} /> : undefined,
      content: (
        <MessageBody
          item={item}
          data={data}
          meta={meta}
          busy={busy}
          isLatestPlan={item.kind === 'plan' && item.plan.id === latestPlanId}
          onUsePlan={setDraft}
          openResults={openResults}
          openSchedule={() => setScheduleOpen(true)}
        />
      ),
    };
  });
  if (draftOpen && !busy) {
    bubbleItems.push({
      key: 'draft',
      role: 'ai',
      header: <BubbleTitle>待你确认的检索式</BubbleTitle>,
      content: (
        <QueryComposer
          data={data}
          initial={draft}
          queryLimits={meta.query_limits}
          maxQueries={meta.max_queries}
          busy={busy}
          onQueued={() => {
            setDraft(null);
            refresh();
          }}
          onCancel={() => setDraft(null)}
        />
      ),
    });
  }
  if (active) {
    bubbleItems.push({
      key: 'active',
      role: 'ai',
      header: <BubbleTitle>{TASK_KIND[active.kind]}</BubbleTitle>,
      content: '',
      loading: true,
      loadingRender: () => (
        <div>
          <Space>
            <Spin size="small" />
            <Typography.Text>
              {active.status === 'queued'
                ? `已排队，马上开始${TASK_KIND[active.kind]}…`
                : `正在${TASK_KIND[active.kind]}…`}
            </Typography.Text>
          </Space>
          <div className="chat-meta" style={{ marginTop: 4 }}>
            任务在后台执行，刷新或离开页面不会重复调用。
          </div>
        </div>
      ),
    });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
      {/* 顶部：任务名 + 弱化元信息；成果与任务设置在右侧 */}
      <div
        style={{
          padding: '10px 20px',
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <Typography.Title
            level={5}
            style={{ margin: 0, flex: 1, minWidth: 0 }}
            ellipsis={{ tooltip: data.product_name }}
          >
            {data.product_name}
          </Typography.Title>
          <Button
            size="small"
            icon={<ProfileOutlined />}
            onClick={() => openResults('candidates')}
          >
            {`成果 · 候选 ${data.candidates.length}`}
          </Button>
          <Dropdown menu={{ items: taskMenu, onClick: onTaskMenu }} trigger={['click']}>
            <Button
              size="small"
              icon={<EllipsisOutlined />}
              aria-label="任务设置"
              loading={acting === 'move'}
            />
          </Dropdown>
        </div>
        <div style={{ marginTop: 2 }}>
          <MetaLine
            parts={[
              IP.name,
              workspaceName,
              `MCP 预算 ${data.budget.used}/${data.budget.per_research}`,
              `全站今天 ${data.budget.daily_used}/${data.budget.daily_limit}`,
              scheduleLabel && `定时复检 ${scheduleLabel}`,
            ]}
          />
        </div>
      </div>

      {/* 消息流：气泡、滚动到底部由 X 的 Bubble.List 负责 */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          background: token.colorBgContainer,
        }}
      >
        {(!data.mcp_configured || !data.llm_configured) && (
          <div
            style={{
              maxWidth: 880,
              width: '100%',
              margin: '0 auto',
              padding: '12px 20px 0',
              display: 'grid',
              gap: 8,
            }}
          >
            {!data.mcp_configured && (
              <Alert
                type="warning"
                showIcon
                title="智慧芽接口未配置，检索与取证不可用，请联系管理员。"
              />
            )}
            {!data.llm_configured && (
              <Alert
                type="info"
                showIcon
                title="大模型未配置：不能自动写检索方案、分析或回答追问，但可以手写检索式完成检索与签署。"
              />
            )}
          </div>
        )}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            maxWidth: 880,
            width: '100%',
            margin: '0 auto',
            padding: '16px 20px 0',
          }}
        >
          <Bubble.List autoScroll style={{ height: '100%' }} items={bubbleItems} role={roles} />
        </div>
      </div>

      {/* 输入区：当前主要动作 + 更多 + 追问 */}
      <div
        style={{
          padding: '10px 20px 16px',
          borderTop: `1px solid ${token.colorBorderSecondary}`,
          background: token.colorBgContainer,
        }}
      >
        <div style={{ maxWidth: 880, margin: '0 auto' }}>
          <Space wrap size={8} style={{ marginBottom: 8 }}>
            {primary && (
              <Tooltip title={primary.disabledReason || primary.tip}>
                <Button
                  size="small"
                  type="primary"
                  icon={primary.icon}
                  disabled={!!primary.disabledReason}
                  loading={primary.loading}
                  onClick={primary.onClick}
                >
                  {primary.label}
                </Button>
              </Tooltip>
            )}
            <Dropdown
              trigger={['click']}
              menu={{
                items: moreItems,
                onClick: ({ key }) => actions[key as ActionKey].onClick(),
              }}
            >
              <Button size="small">
                <Space size={4}>
                  {primary ? '更多操作' : '操作'}
                  <DownOutlined />
                </Space>
              </Button>
            </Dropdown>
          </Space>
          <Sender
            value={text}
            onChange={(v) => setText(v.slice(0, ASK_MAX))}
            onSubmit={send}
            loading={acting === 'ask'}
            disabled={!data.llm_configured || busy}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={
              !data.llm_configured
                ? '大模型未配置，暂不能追问'
                : busy
                  ? '等当前任务完成后再发送'
                  : '向 IP 专家追问，例如：哪几件和我的产品最像？（只调用大模型，不计 MCP 预算；Shift+Enter 换行）'
            }
          />
        </div>
      </div>

      <ResultsDrawer
        open={results.open}
        section={results.section}
        onSection={(section) => setResults((r) => ({ ...r, section }))}
        onClose={() => setResults((r) => ({ ...r, open: false }))}
        data={data}
        meta={meta}
        busy={busy}
        onChanged={refresh}
      />
      <ScheduleModal
        open={scheduleOpen}
        data={data}
        onClose={() => setScheduleOpen(false)}
        onSaved={refresh}
      />
    </div>
  );
}
