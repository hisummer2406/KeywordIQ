import type { ResearchDetail } from '@/services/opportunityIp';

/**
 * 任务进行到哪一步，输入区就突出哪一个动作（change refine-opportunity-chat-ui 决策 D3；
 * admin-spa 场景「按进度突出主要动作」）。其余动作都还在「更多」里，推断不合意只是多点一下。
 *
 * 这里只决定**展示**，不入队任何计费动作：「确认检索式」最多打开待确认卡，检索仍要在卡片里确认调用次数。
 */

export type ActionKey = 'plan' | 'confirmPlan' | 'manual' | 'analysis' | 'review';

export function nextAction(d: ResearchDetail): ActionKey | null {
  if (!d.search_done) {
    if (d.plans.length) return 'confirmPlan';
    return d.llm_configured ? 'plan' : 'manual';
  }
  if (!d.analyses.length && d.llm_configured) return 'analysis';
  if (d.markets.some((m) => !(d.reviews[m] ?? []).length)) return 'review';
  return null;
}
