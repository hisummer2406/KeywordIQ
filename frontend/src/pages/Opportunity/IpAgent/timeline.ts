import type { Review } from '@/services/risk';
import type {
  AgentOutput,
  AnalysisContent,
  IpMessage,
  IpQuery,
  IpTask,
  PlanContent,
  ResearchDetail,
} from '@/services/opportunityIp';

/**
 * 任务对话的消息流（change add-opportunity-ip-agent 决策 10）。
 *
 * 消息流由服务端已存的事实拼出，不另存一份：任务简报 → 检索方案 → 每次检索 → 取证 → 分析 →
 * 人工意见，中间穿插追问与回答；失败或结果未知、且没有留下产物的任务显示为系统消息。在途任务
 * 不进消息流，由对话底部的进度提示单独显示。
 */

export type TimelineItem =
  | { kind: 'brief'; key: string; at: string }
  | { kind: 'plan'; key: string; at: string; plan: AgentOutput<PlanContent> }
  | { kind: 'search'; key: string; at: string; task: IpTask; queries: IpQuery[]; newCandidates: number }
  | { kind: 'detail'; key: string; at: string; task: IpTask }
  | { kind: 'analysis'; key: string; at: string; analysis: AgentOutput<AnalysisContent> }
  | { kind: 'message'; key: string; at: string; message: IpMessage }
  | { kind: 'review'; key: string; at: string; review: Review }
  | { kind: 'task-failed'; key: string; at: string; task: IpTask }
  | { kind: 'schedule-skipped'; key: string; at: string; reason: string };

const TERMINAL = new Set(['completed', 'failed', 'unknown']);

export function buildTimeline(d: ResearchDetail): TimelineItem[] {
  const items: TimelineItem[] = [{ kind: 'brief', key: 'brief', at: d.created_at }];
  for (const plan of d.plans) items.push({ kind: 'plan', key: `plan-${plan.id}`, at: plan.created_at, plan });
  for (const analysis of d.analyses) {
    items.push({ kind: 'analysis', key: `analysis-${analysis.id}`, at: analysis.created_at, analysis });
  }
  for (const message of d.messages) {
    items.push({ kind: 'message', key: `msg-${message.id}`, at: message.created_at, message });
  }
  for (const list of Object.values(d.reviews)) {
    for (const review of list) items.push({ kind: 'review', key: `review-${review.id}`, at: review.created_at, review });
  }

  const firstSeen = new Map<number, number>(); // query_id → 首次出现在这条检索式的候选数
  for (const c of d.candidates) firstSeen.set(c.query_id, (firstSeen.get(c.query_id) ?? 0) + 1);
  const outputTasks = new Set([...d.plans, ...d.analyses].map((o) => o.task_id));
  const answered = new Set(d.messages.filter((m) => m.role === 'agent').map((m) => m.task_id));

  for (const task of d.tasks) {
    if (!TERMINAL.has(task.status)) continue;
    const at = task.finished_at ?? task.created_at;
    if (task.kind === 'search') {
      const queries = d.queries.filter((q) => q.task_id === task.id);
      if (!queries.length) {
        // 认领时就失败（例如未配置），一条检索都没发出
        items.push({ kind: 'task-failed', key: `task-${task.id}`, at, task });
        continue;
      }
      const newCandidates = queries.reduce((n, q) => n + (firstSeen.get(q.id) ?? 0), 0);
      items.push({ kind: 'search', key: `task-${task.id}`, at, task, queries, newCandidates });
    } else if (task.kind === 'detail') {
      items.push({ kind: 'detail', key: `task-${task.id}`, at, task });
    } else if (!outputTasks.has(task.id) && !answered.has(task.id)) {
      // plan / analyze / ask 没留下产物：成功也不该出现（校验失败会记为 failed），这里只剩失败与未知
      items.push({ kind: 'task-failed', key: `task-${task.id}`, at, task });
    }
  }

  const s = d.schedule;
  if (s?.last_status === 'skipped' && s.last_run_at) {
    items.push({ kind: 'schedule-skipped', key: `sched-${s.last_run_at}`, at: s.last_run_at, reason: s.last_reason ?? '' });
  }

  // 同一时刻：先简报、再用户消息，其余按插入顺序（Array.prototype.sort 是稳定排序）
  const weight = (i: TimelineItem) => (i.kind === 'brief' ? 0 : i.kind === 'message' && i.message.role === 'user' ? 1 : 2);
  // 按时间值比较，不按字符串：时间来自数据库与应用两处，小数秒位数可能不同
  const t = (i: TimelineItem) => Date.parse(i.at) || 0;
  return items.sort((a, b) => t(a) - t(b) || weight(a) - weight(b));
}

/** 本调研已成功执行过的检索式（去重，保留最近一次的条数上限）——定时复检只能从这里选。 */
export function executedQueries(d: ResearchDetail): { query_text: string; limit: number }[] {
  const out = new Map<string, number>();
  for (const q of d.queries) if (q.status === 'success') out.set(q.query_text, q.limit);
  return [...out].map(([query_text, limit]) => ({ query_text, limit }));
}
