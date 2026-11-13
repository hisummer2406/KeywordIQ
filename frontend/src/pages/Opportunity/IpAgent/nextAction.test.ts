import { describe, expect, it } from 'vitest';
import type { ResearchDetail } from '@/services/opportunityIp';
import { nextAction } from './nextAction';

/** 按任务进度突出的主要动作（change refine-opportunity-chat-ui 决策 D3 的每一行条件）。 */

const d = (over: Partial<ResearchDetail> = {}) =>
  ({
    llm_configured: true,
    search_done: false,
    plans: [],
    analyses: [],
    markets: ['US', 'DE'],
    reviews: {},
    ...over,
  }) as unknown as ResearchDetail;

const plan = { id: 1 } as ResearchDetail['plans'][number];
const analysis = { id: 1 } as ResearchDetail['analyses'][number];
const review = { id: 1 } as ResearchDetail['reviews'][string][number];

describe('nextAction', () => {
  it('没有方案也没有成功检索：生成检索方案', () => {
    expect(nextAction(d())).toBe('plan');
  });

  it('大模型未配置：手写检索式', () => {
    expect(nextAction(d({ llm_configured: false }))).toBe('manual');
  });

  it('已有方案、还没成功检索：确认检索式', () => {
    expect(nextAction(d({ plans: [plan] }))).toBe('confirmPlan');
  });

  it('已有成功检索、没有分析：生成风险分析', () => {
    expect(nextAction(d({ search_done: true, plans: [plan] }))).toBe('analysis');
  });

  it('已有成功检索、大模型未配置：直接签署人工意见', () => {
    expect(nextAction(d({ search_done: true, llm_configured: false }))).toBe('review');
  });

  it('已有分析、还有市场没签：签署人工意见', () => {
    expect(nextAction(d({ search_done: true, analyses: [analysis], reviews: { US: [review] } }))).toBe('review');
  });

  it('全部市场都已签署：没有主要动作', () => {
    expect(
      nextAction(d({ search_done: true, analyses: [analysis], reviews: { US: [review], DE: [review] } })),
    ).toBeNull();
  });
});
