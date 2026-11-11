import { describe, expect, it } from 'vitest';
import type { IpTask, ResearchDetail } from '@/services/opportunityIp';
import { scheduleText, taskErrorText } from './ipLabels';
import { buildTimeline, executedQueries } from './timeline';

/**
 * 任务对话消息流（change add-opportunity-ip-agent 决策 10、12、13）：由已存事实拼出，失败任务
 * 显示为系统消息，在途任务不进消息流；定时复检只能选已成功执行过的检索式。
 */

const task = (id: number, kind: IpTask['kind'], status: IpTask['status'], at: string, extra: Partial<IpTask> = {}): IpTask => ({
  id,
  kind,
  status,
  error_category: null,
  planned_mcp_calls: 0,
  scheduled: false,
  created_at: at,
  started_at: at,
  finished_at: status === 'queued' || status === 'running' ? null : at,
  ...extra,
});

function detail(over: Partial<ResearchDetail> = {}): ResearchDetail {
  return {
    id: 1,
    product_name: '杯',
    description: null,
    keywords: [],
    loc_classes: [],
    jurisdictions: ['US'],
    markets: ['US'],
    workspace_id: null,
    created_at: '2026-11-11T01:00:00+00:00',
    jurisdiction_details: [],
    mcp_configured: true,
    llm_configured: true,
    budget: { per_research: 20, used: 0, daily_limit: 200, daily_used: 0 },
    active_task: null,
    tasks: [],
    plans: [],
    analyses: [],
    queries: [],
    candidates: [],
    coverage: { queries_total: 0, queries_success: 0, queries_failed: 0, queries_unknown: 0, candidates: 0, detailed: {} as any },
    search_done: false,
    reviews: { US: [] },
    calls: [],
    messages: [],
    schedule: null,
    ...over,
  };
}

describe('buildTimeline', () => {
  it('按时间排序，检索卡带本次新增候选数，在途任务不进消息流', () => {
    const d = detail({
      tasks: [
        task(3, 'search', 'queued', '2026-11-11T04:00:00+00:00'),
        task(2, 'search', 'completed', '2026-11-11T03:00:00.123+00:00', { scheduled: true }),
        task(1, 'plan', 'completed', '2026-11-11T02:00:00+00:00'),
      ],
      plans: [{ id: 9, task_id: 1, model: 'm', content: { queries: [], assumptions: [] }, coverage: null, created_at: '2026-11-11T02:00:00+00:00' }],
      queries: [
        { id: 5, task_id: 2, query_text: 'TTL:(cup)', limit: 10, status: 'success', error_category: null, total_count: 3, returned_count: 3, fetched_at: '' },
      ],
      candidates: [1, 2, 3].map((i) => ({
        id: i, query_id: 5, rank: i, patent_id: `P${i}`, pn: null, title: null, authority: null, apno: null,
        apdt: null, pbdt: null, assignee: null, fetched_at: '', details: {},
      })),
    });
    const items = buildTimeline(d);
    expect(items.map((i) => i.kind)).toEqual(['brief', 'plan', 'search']);
    const search = items[2];
    expect(search.kind === 'search' && search.newCandidates).toBe(3);
    expect(search.kind === 'search' && search.task.scheduled).toBe(true);
  });

  it('没有产物的失败任务显示为系统消息；有回答的追问不重复显示', () => {
    const d = detail({
      tasks: [
        task(2, 'ask', 'completed', '2026-11-11T03:00:00+00:00'),
        task(1, 'analyze', 'unknown', '2026-11-11T02:00:00+00:00', { error_category: 'timeout' }),
      ],
      messages: [
        { id: 1, task_id: 2, role: 'user', content: '?', model: null, cited_candidate_ids: [], created_at: '2026-11-11T02:30:00+00:00' },
        { id: 2, task_id: 2, role: 'agent', content: '答', model: 'm', cited_candidate_ids: [], created_at: '2026-11-11T03:00:00+00:00' },
      ],
    });
    expect(buildTimeline(d).map((i) => i.kind)).toEqual(['brief', 'task-failed', 'message', 'message']);
    expect(taskErrorText('ask', 'timeout')).toContain('大模型');
  });

  it('定时复检被跳过时显示原因', () => {
    const d = detail({
      schedule: {
        id: 1, research_id: 1, frequency: 'weekly', weekday: 0, day_of_month: null, hour: 9, queries: [], enabled: true,
        next_run_at: '', last_run_at: '2026-11-12T01:00:00+00:00', last_status: 'skipped',
        last_reason: '本调研 MCP 调用预算不足', last_task_id: null, created_at: '', updated_at: '',
      },
    });
    const last = buildTimeline(d).at(-1);
    expect(last?.kind === 'schedule-skipped' && last.reason).toContain('预算不足');
  });
});

describe('定时复检', () => {
  it('只能选已成功执行过的检索式，去重', () => {
    const q = (id: number, query_text: string, status: 'success' | 'failed', limit = 10) => ({
      id, task_id: 1, query_text, limit, status, error_category: null, total_count: 0, returned_count: 0, fetched_at: '',
    });
    const d = detail({ queries: [q(1, 'A', 'success'), q(2, 'B', 'failed'), q(3, 'A', 'success', 50)] });
    expect(executedQueries(d)).toEqual([{ query_text: 'A', limit: 50 }]);
  });

  it('频率说明', () => {
    expect(scheduleText({ frequency: 'weekly', weekday: 0, day_of_month: null, hour: 9 })).toBe('每周一 09:00');
    expect(scheduleText({ frequency: 'monthly', weekday: null, day_of_month: 15, hour: 21 })).toBe('每月 15 日 21:00');
  });
});
