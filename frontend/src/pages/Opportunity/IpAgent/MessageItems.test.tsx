import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { IpMeta, IpQuery, IpTask, ResearchDetail } from '@/services/opportunityIp';
import type { Review } from '@/services/risk';
import MessageBody from './MessageItems';
import type { TimelineItem } from './timeline';

/**
 * 时间线各类卡片的渲染（change adopt-antdx-opportunity-ui，tasks 2.3）：换成 X 的 Bubble.List 后，
 * 每类卡片的关键文案与按钮必须仍在；模型文本一律按字面显示。
 */

vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));

const AT = '2026-11-11T02:00:00+00:00';

const task = (kind: IpTask['kind'], status: IpTask['status'], over: Partial<IpTask> = {}): IpTask => ({
  id: 1,
  kind,
  status,
  error_category: null,
  planned_mcp_calls: 0,
  scheduled: false,
  created_at: AT,
  started_at: AT,
  finished_at: AT,
  ...over,
});

const data = {
  id: 1,
  product_name: '折叠杯',
  description: null,
  keywords: [],
  loc_classes: [],
  markets: ['US'],
  jurisdiction_details: [{ code: 'US', name: '美国', note: '' }],
  candidates: [{ id: 5, pn: 'US D123456', patent_id: 'p5' }],
  schedule: null,
} as unknown as ResearchDetail;

const meta = { markets: [{ code: 'US', name: '美国' }] } as unknown as IpMeta;

function show(item: TimelineItem, over: Partial<Parameters<typeof MessageBody>[0]> = {}) {
  const openResults = vi.fn();
  const openSchedule = vi.fn();
  render(
    <MessageBody
      item={item}
      data={data}
      meta={meta}
      busy={false}
      isLatestPlan={false}
      onUsePlan={vi.fn()}
      openResults={openResults}
      openSchedule={openSchedule}
      {...over}
    />,
  );
  return { openResults, openSchedule };
}

const query = (over: Partial<IpQuery> = {}): IpQuery =>
  ({
    id: 1,
    task_id: 1,
    query_text: 'TTL:(mug)',
    status: 'success',
    total_count: 12,
    returned_count: 10,
    error_category: null,
    ...over,
  }) as IpQuery;

describe('时间线卡片', () => {
  it('检索：默认折叠为摘要，失败条数可见；展开后每条检索式带状态与结果文案，按钮可用', () => {
    const { openResults, openSchedule } = show({
      kind: 'search',
      key: 's',
      at: AT,
      task: task('search', 'completed'),
      queries: [query(), query({ id: 2, query_text: 'TA:(cup)', status: 'failed', error_category: 'not_configured' })],
      newCandidates: 3,
    });
    // 折叠时：摘要里有条数、失败数与新增候选，逐条检索式不展示
    expect(screen.getByText('执行检索 2 条')).toBeTruthy();
    expect(screen.getByText(/失败 1/)).toBeTruthy();
    expect(screen.getByText(/新增 3 件候选（已按专利去重）/)).toBeTruthy();
    expect(screen.queryByText('TTL:(mug)')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /查看检索式/ }));
    expect(screen.getByText('TTL:(mug)')).toBeTruthy();
    expect(screen.getByText('来源共 12 条，已读取 10 条')).toBeTruthy();
    expect(screen.getByText('智慧芽接口未配置，请联系管理员')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看候选并取证' }));
    expect(openResults).toHaveBeenCalledWith('candidates');
    fireEvent.click(screen.getByRole('button', { name: '设为定时复检' }));
    expect(openSchedule).toHaveBeenCalled();
  });

  it('取证：显示计划调用次数与失败原因，按钮打开候选', () => {
    const { openResults } = show({
      kind: 'detail',
      key: 'd',
      at: AT,
      task: task('detail', 'failed', { planned_mcp_calls: 6, error_category: 'not_configured' }),
    });
    expect(screen.getByText(/本次计划调用智慧芽 6 次。/)).toBeTruthy();
    expect(screen.getByText(/智慧芽接口未配置，请联系管理员/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看证据' }));
    expect(openResults).toHaveBeenCalledWith('candidates');
  });

  it('分析：覆盖事实、信号、缺口都在，按钮去签署；模型文本按字面显示', () => {
    const evil = '<img src="x" onerror="window.__y=1">摘要';
    const { openResults } = show({
      kind: 'analysis',
      key: 'a',
      at: AT,
      analysis: {
        id: 1,
        task_id: 2,
        model: 'm',
        created_at: AT,
        coverage: { queries_total: 2, queries_success: 1, queries_failed: 1, queries_unknown: 0, candidates: 4, detailed: {} as never },
        content: {
          summary: evil,
          signals: [{ candidate_id: 5, relevance: 'high', reason: '外形相似' }],
          coverage_gaps: ['只查了美国'],
          unknowns: ['无法判断图片'],
          next_steps: ['补查欧盟'],
        },
      },
    });
    expect(screen.getByText(evil)).toBeTruthy();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(screen.getByText('高相关')).toBeTruthy();
    expect(screen.getByText('US D123456')).toBeTruthy();
    expect(screen.getByText('外形相似')).toBeTruthy();
    expect(screen.getByText('1/2 条成功')).toBeTruthy();
    expect(screen.getByText('只查了美国')).toBeTruthy();
    expect(screen.getByText('补查欧盟')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '去签署人工意见' }));
    expect(openResults).toHaveBeenCalledWith('reviews');
  });

  it('签署：签署人、市场、风险与决定、依据', () => {
    const review = {
      id: 1,
      market: 'US',
      risk_level: 'high',
      decision: 'not_recommended',
      basis: '与候选 US D123456 高度相似',
      uncovered_scope: '',
      conditions: null,
      evidence_candidate_ids: [5],
      review_date: null,
      signer_id: 1,
      signer_name: '张三',
      created_at: AT,
    } as Review;
    show({ kind: 'review', key: 'r', at: AT, review });
    expect(screen.getByText(/张三 签署了 美国 的人工意见：/)).toBeTruthy();
    expect(screen.getByText('风险 高')).toBeTruthy();
    expect(screen.getByText('不建议上架')).toBeTruthy();
    expect(screen.getByText('与候选 US D123456 高度相似')).toBeTruthy();
  });

  it('任务失败：失败与结果未知分开显示', () => {
    show({ kind: 'task-failed', key: 'f', at: AT, task: task('ask', 'failed', { error_category: 'x' }) });
    expect(screen.getByText('回答追问：失败，回答未生成')).toBeTruthy();
    expect(screen.getByText('大模型调用失败')).toBeTruthy();
  });

  it('任务结果未知：不写成失败', () => {
    show({ kind: 'task-failed', key: 'u', at: AT, task: task('search', 'unknown', { error_category: 'timeout' }) });
    expect(screen.getByText('执行检索：结果未知')).toBeTruthy();
    expect(screen.queryByText(/执行检索：失败/)).toBeNull();
  });

  it('Agent 回答：渲染受限 Markdown，引用标签在底部，链接与图片不产生元素', () => {
    const msg = (content: string, role: 'user' | 'agent' = 'agent'): TimelineItem => ({
      kind: 'message',
      key: `m-${role}`,
      at: AT,
      message: { id: 1, task_id: 2, role, content, model: 'm', cited_candidate_ids: [5], created_at: AT },
    });
    const { container } = render(
      <MessageBody
        item={msg('## 结论\n\n- 和 US D123456 外形接近\n\n[来源](javascript:alert(1)) ![图](https://e.com/a.png)')}
        data={data}
        meta={meta}
        busy={false}
        isLatestPlan={false}
        onUsePlan={vi.fn()}
        openResults={vi.fn()}
        openSchedule={vi.fn()}
      />,
    );
    expect(container.querySelector('h2')?.textContent).toBe('结论');
    expect(container.querySelectorAll('li')).toHaveLength(1);
    expect(container.querySelector('a, img')).toBeNull();
    expect(screen.getByText('US D123456')).toBeTruthy();
  });

  it('用户追问与检索方案仍按纯文本，不渲染 Markdown', () => {
    const { container } = render(
      <>
        <MessageBody
          item={{
            kind: 'message',
            key: 'u',
            at: AT,
            message: { id: 1, task_id: 2, role: 'user', content: '# 不是标题', model: null, cited_candidate_ids: [], created_at: AT },
          }}
          data={data}
          meta={meta}
          busy={false}
          isLatestPlan={false}
          onUsePlan={vi.fn()}
          openResults={vi.fn()}
          openSchedule={vi.fn()}
        />
        <MessageBody
          item={{
            kind: 'plan',
            key: 'p',
            at: AT,
            plan: {
              id: 1,
              task_id: 3,
              model: 'm',
              coverage: null,
              created_at: AT,
              content: { queries: [{ query_text: 'TTL:(mug)', rationale: '**最窄** [x](https://e.com)' }], assumptions: [] },
            },
          }}
          data={data}
          meta={meta}
          busy={false}
          isLatestPlan
          onUsePlan={vi.fn()}
          openResults={vi.fn()}
          openSchedule={vi.fn()}
        />
      </>,
    );
    expect(container.querySelector('h1, strong, a')).toBeNull();
    expect(screen.getByText('# 不是标题')).toBeTruthy();
    expect(screen.getByText('**最窄** [x](https://e.com)')).toBeTruthy();
  });

  it('定时复检跳过：显示原因', () => {
    show({ kind: 'schedule-skipped', key: 'k', at: AT, reason: '上次任务还在执行' });
    expect(screen.getByText(/定时复检已跳过/)).toBeTruthy();
    expect(screen.getByText('上次任务还在执行')).toBeTruthy();
  });
});
