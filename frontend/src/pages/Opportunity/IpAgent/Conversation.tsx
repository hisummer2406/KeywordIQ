import {
  ClockCircleOutlined,
  EditOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  ProfileOutlined,
  SendOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Alert, Button, Input, Result, Select, Space, Spin, Tag, Tooltip, Typography, theme } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { EXPERT_BY_KEY } from '../experts';
import { scheduleText, TASK_KIND } from './ipLabels';
import MessageItem, { Bubble } from './MessageItems';
import QueryComposer from './QueryComposer';
import ResultsDrawer, { type ResultsSection } from './ResultsDrawer';
import ScheduleModal from './ScheduleModal';
import { buildTimeline } from './timeline';

/**
 * IP 专家任务对话（change add-opportunity-ip-agent 决策 10、12）。
 *
 * - 消息流由已存事实拼出（timeline.ts）；有在途任务时每 3 秒轮询详情（只读库，不产生外部调用），没有就停。
 * - 底部输入框发送的是**追问**（只调用大模型）；检索与取证必须在卡片里确认调用次数后才入队。
 */

const POLL_MS = 3000;
const IP = EXPERT_BY_KEY.ip;

interface Props {
  id: number;
  meta: IpMeta;
  workspaces: Workspace[];
  /** 任务状态或所属工作空间变化后，通知左栏刷新 */
  onChanged: () => void;
  onBack: () => void;
}

export default function Conversation({ id, meta, workspaces, onChanged, onBack }: Props) {
  const { token } = theme.useToken();
  const [data, setData] = useState<ResearchDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [results, setResults] = useState<{ open: boolean; section: ResultsSection }>({ open: false, section: 'candidates' });
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [text, setText] = useState('');
  const [acting, setActing] = useState<string | null>(null);
  const tokens = useRef({ plan: newToken(), analysis: newToken(), ask: newToken() });
  const scroller = useRef<HTMLDivElement>(null);

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
    if (latestPlan && data && data.queries.length === 0) setDraft(latestPlan.content.queries.map((q) => q.query_text));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestPlan?.id]);

  const items = useMemo(() => (data ? buildTimeline(data) : []), [data]);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length, active?.id, draft === null]);

  if (missing) {
    return <Result status="404" title="任务不存在" extra={<Button onClick={onBack}>返回</Button>} />;
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

  const llmTip = data.llm_configured ? '' : '大模型未配置';
  const analysisTip = llmTip || (!data.search_done ? '先成功执行至少一条检索' : '');
  const latestPlanId = latestPlan?.id;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
      {/* 顶部：任务、专家、工作空间、预算 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '12px 20px',
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          flexWrap: 'wrap',
        }}
      >
        <Typography.Title level={5} style={{ margin: 0 }} ellipsis={{ tooltip: data.product_name }}>
          {data.product_name}
        </Typography.Title>
        <Tag color={IP.color}>{IP.name}</Tag>
        <Select
          size="small"
          style={{ width: 160 }}
          suffixIcon={<FolderOpenOutlined />}
          value={data.workspace_id ?? 0}
          options={[{ value: 0, label: '未归类' }, ...workspaces.map((w) => ({ value: w.id, label: w.name }))]}
          onChange={(v) => act('move', () => moveResearch(data.id, v || null))}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {`MCP 预算 ${data.budget.used}/${data.budget.per_research} · 全站今天 ${data.budget.daily_used}/${data.budget.daily_limit}`}
        </Typography.Text>
        <div style={{ flex: 1 }} />
        <Tooltip title={data.schedule ? `下次执行 ${dt(data.schedule.next_run_at)}` : '把已执行的检索式设为每周或每月复检'}>
          <Button size="small" icon={<ClockCircleOutlined />} onClick={() => setScheduleOpen(true)}>
            {data.schedule ? `${scheduleText(data.schedule)}${data.schedule.enabled ? '' : '（已暂停）'}` : '定时复检'}
          </Button>
        </Tooltip>
        <Button size="small" type="primary" ghost icon={<ProfileOutlined />} onClick={() => setResults({ open: true, section: 'candidates' })}>
          {`成果 · 候选 ${data.candidates.length}`}
        </Button>
      </div>

      {/* 消息流 */}
      <div ref={scroller} style={{ flex: 1, overflowY: 'auto', padding: '20px 20px 8px', background: token.colorBgLayout }}>
        <div style={{ maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!data.mcp_configured && <Alert type="warning" showIcon title="智慧芽接口未配置，检索与取证不可用，请联系管理员。" />}
          {!data.llm_configured && (
            <Alert type="info" showIcon title="大模型未配置：不能自动写检索方案、分析或回答追问，但可以手写检索式完成检索与签署。" />
          )}
          {items.map((item) => (
            <MessageItem
              key={item.key}
              item={item}
              data={data}
              meta={meta}
              busy={busy}
              isLatestPlan={item.kind === 'plan' && item.plan.id === latestPlanId}
              onUsePlan={setDraft}
              openResults={(section) => setResults({ open: true, section })}
              openSchedule={() => setScheduleOpen(true)}
            />
          ))}
          {draft !== null && !busy && (
            <Bubble side="agent" wide title="待你确认的检索式">
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
            </Bubble>
          )}
          {active && (
            <Bubble side="agent" title={TASK_KIND[active.kind]}>
              <Space>
                <Spin size="small" />
                <Typography.Text>
                  {active.status === 'queued' ? `已排队，马上开始${TASK_KIND[active.kind]}…` : `正在${TASK_KIND[active.kind]}…`}
                </Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
                任务在后台执行，刷新或离开页面不会重复调用。
              </Typography.Text>
            </Bubble>
          )}
        </div>
      </div>

      {/* 输入区：快捷动作 + 追问 */}
      <div style={{ padding: '10px 20px 16px', borderTop: `1px solid ${token.colorBorderSecondary}`, background: token.colorBgContainer }}>
        <div style={{ maxWidth: 960, margin: '0 auto' }}>
          <Space wrap size={8} style={{ marginBottom: 8 }}>
            <Tooltip title={llmTip || '调用一次大模型，根据产品信息写检索式建议（不调用智慧芽）'}>
              <Button size="small" icon={<ThunderboltOutlined />} disabled={!!llmTip || busy} loading={acting === 'plan'} onClick={plan}>
                {data.plans.length ? '重新生成检索方案' : '生成检索方案'}
              </Button>
            </Tooltip>
            <Button size="small" icon={<EditOutlined />} disabled={busy || draft !== null} onClick={() => setDraft([])}>
              手写检索式
            </Button>
            <Tooltip title={analysisTip || '调用一次大模型，基于已取得的证据写风险分析'}>
              <Button size="small" icon={<FileSearchOutlined />} disabled={!!analysisTip || busy} loading={acting === 'analysis'} onClick={analyze}>
                {data.analyses.length ? '重新生成风险分析' : '生成风险分析'}
              </Button>
            </Tooltip>
            <Button size="small" onClick={() => setResults({ open: true, section: 'reviews' })}>
              签署人工意见
            </Button>
          </Space>
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <Input.TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={2000}
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={!data.llm_configured}
              placeholder={
                data.llm_configured
                  ? '向 IP 专家追问，例如：哪几件和我的产品最像？还缺哪些检索？（Enter 发送，Shift+Enter 换行）'
                  : '大模型未配置，暂不能追问'
              }
              onPressEnter={(e) => {
                if (e.shiftKey || e.nativeEvent.isComposing) return;
                e.preventDefault();
                send();
              }}
            />
            <Tooltip title={busy ? '等当前任务完成后再发送' : '只调用大模型，不调用智慧芽、不计 MCP 预算'}>
              <Button
                type="primary"
                icon={<SendOutlined />}
                disabled={!text.trim() || busy || !data.llm_configured}
                loading={acting === 'ask'}
                onClick={send}
              />
            </Tooltip>
          </div>
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
      <ScheduleModal open={scheduleOpen} data={data} onClose={() => setScheduleOpen(false)} onSaved={refresh} />
    </div>
  );
}
