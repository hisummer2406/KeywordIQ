import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IpMeta, IpTask, ResearchDetail } from '@/services/opportunityIp';
import Conversation from './Conversation';

/**
 * 专家任务对话（change adopt-antdx-opportunity-ui，admin-spa「对话类界面的组件与安全边界」）：
 * 输入框的回车只发追问、不触发检索与取证；忙或大模型未配置时不能提交；模型文本按字面显示。
 */

// services 引入 @umijs/max 的 request；不 mock 会把 Umi 打包器拉进 jsdom
vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));

const api = vi.hoisted(() => ({
  getResearch: vi.fn(),
  askResearch: vi.fn(),
  requestPlan: vi.fn(),
  requestSearch: vi.fn(),
  requestAnalysis: vi.fn(),
  requestDetail: vi.fn(),
  moveResearch: vi.fn(),
}));
vi.mock('@/services/opportunityIp', async (orig) => ({
  ...(await orig<typeof import('@/services/opportunityIp')>()),
  ...api,
}));

const task = (id: number, kind: IpTask['kind'], status: IpTask['status']): IpTask => ({
  id,
  kind,
  status,
  error_category: null,
  planned_mcp_calls: 0,
  scheduled: false,
  created_at: '2026-11-11T02:00:00+00:00',
  started_at: null,
  finished_at: null,
});

function detail(over: Partial<ResearchDetail> = {}): ResearchDetail {
  return {
    id: 1,
    product_name: '折叠杯',
    description: '可折叠的硅胶杯',
    keywords: [],
    loc_classes: [],
    jurisdictions: ['US'],
    markets: ['US'],
    workspace_id: null,
    created_at: '2026-11-11T01:00:00+00:00',
    jurisdiction_details: [{ code: 'US', name: '美国', note: '' }],
    mcp_configured: true,
    llm_configured: true,
    budget: { per_research: 20, used: 0, daily_limit: 200, daily_used: 0 },
    active_task: null,
    tasks: [],
    plans: [],
    analyses: [],
    queries: [],
    candidates: [],
    coverage: { queries_total: 0, queries_success: 0, queries_failed: 0, queries_unknown: 0, candidates: 0, detailed: {} as never },
    search_done: false,
    reviews: { US: [] },
    calls: [],
    messages: [],
    schedule: null,
    ...over,
  };
}

const meta = {
  mcp_configured: true,
  llm_configured: true,
  llm_model: 'm',
  markets: [{ code: 'US', name: '美国' }],
  jurisdictions: [],
  recommendations: {},
  query_limits: [10, 20],
  max_queries: 5,
  max_detail_batch: 5,
  detail_tools: [],
  budget: { per_research: 20, daily_limit: 200, daily_used: 0 },
} as unknown as IpMeta;

async function mount(d: ResearchDetail) {
  api.getResearch.mockResolvedValue(d);
  await act(async () => {
    render(<Conversation id={1} meta={meta} workspaces={[]} onChanged={() => undefined} onBack={() => undefined} />);
  });
}

const box = () => screen.getByRole('textbox') as HTMLTextAreaElement;

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.askResearch.mockResolvedValue({ task_id: 9, status: 'queued' });
});

describe('追问输入', () => {
  it('回车只创建一次追问，不触发检索或取证', async () => {
    await mount(detail());
    fireEvent.change(box(), { target: { value: '哪几件最像？' } });
    await act(async () => {
      fireEvent.keyDown(box(), { key: 'Enter', code: 'Enter' });
    });
    expect(api.askResearch).toHaveBeenCalledTimes(1);
    expect(api.askResearch.mock.calls[0][1].content).toBe('哪几件最像？');
    expect(api.requestSearch).not.toHaveBeenCalled();
    expect(api.requestDetail).not.toHaveBeenCalled();
    expect(api.requestPlan).not.toHaveBeenCalled();
  });

  it('输入法组合态下回车不提交', async () => {
    await mount(detail());
    fireEvent.change(box(), { target: { value: '外观' } });
    await act(async () => {
      fireEvent.compositionStart(box());
      fireEvent.keyDown(box(), { key: 'Enter', code: 'Enter', isComposing: true });
    });
    expect(api.askResearch).not.toHaveBeenCalled();
    expect(box().value).toBe('外观');
  });

  it('有在途任务时输入框不可用，并说明原因，不发请求', async () => {
    await mount(detail({ active_task: task(5, 'search', 'running') }));
    expect(box().disabled).toBe(true);
    expect(box().placeholder).toContain('等当前任务完成');
    expect(api.askResearch).not.toHaveBeenCalled();
  });

  it('大模型未配置时输入框不可用', async () => {
    await mount(detail({ llm_configured: false }));
    expect(box().disabled).toBe(true);
    expect(box().placeholder).toContain('大模型未配置');
  });
});

describe('消息流', () => {
  it('模型文本按字面显示，不产生 DOM 元素', async () => {
    const evil = '<script>window.__x=1</script><img src="x" onerror="window.__x=2">[链接](http://e.com)';
    await mount(
      detail({
        messages: [
          { id: 1, task_id: 2, role: 'user', content: '问', model: null, cited_candidate_ids: [], created_at: '2026-11-11T02:00:00+00:00' },
          { id: 2, task_id: 2, role: 'agent', content: evil, model: 'm', cited_candidate_ids: [], created_at: '2026-11-11T02:01:00+00:00' },
        ],
      }),
    );
    expect(screen.getByText(evil)).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(document.querySelector('a[href="http://e.com"]')).toBeNull();
  });

  it('简报、方案、系统消息与待确认卡都在', async () => {
    await mount(
      detail({
        plans: [
          {
            id: 1,
            task_id: 3,
            model: 'm',
            coverage: null,
            created_at: '2026-11-11T01:30:00+00:00',
            content: { queries: [{ query_text: 'TTL:(mug)', rationale: '最窄' }], assumptions: [] },
          },
        ],
        tasks: [{ ...task(4, 'detail', 'failed'), error_category: 'upstream' }],
      }),
    );
    // 页头标题与简报各一处
    expect(screen.getAllByText('折叠杯').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('TTL:(mug)').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /确认并编辑检索式/ })).toBeTruthy();
    // 方案到达且还没检索过：方案被填进待确认卡片
    expect(screen.getByText('待你确认的检索式')).toBeTruthy();
  });

  it('有在途任务时显示进度文案，且不再显示待确认卡', async () => {
    await mount(detail({ active_task: task(6, 'search', 'queued') }));
    expect(screen.getByText(/已排队，马上开始/)).toBeTruthy();
    expect(screen.queryByText('待你确认的检索式')).toBeNull();
  });
});
