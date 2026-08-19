import { act, fireEvent, render, screen } from '@testing-library/react';
import AnalysisPage from '@/pages/Analysis';

/**
 * 分析查询屏的渲染帮手。与 `analysisMocks` 分成两个模块是有原因的，别合并——
 * 见 `analysisMocks.ts` 顶部关于循环依赖的说明。
 */

/**
 * 渲染并把进屏时的异步 effect（作用域解析）跑完，避免 act 警告淹没真实失败。
 *
 * **不替你点「立即查询」**：首屏会按默认作用域自动查一次，渲染完就已经是「查过一次」
 * 的状态，绝大多数用例断言的正是这之后的行为。替它再点一次会让请求数多一次，
 * 「首屏是否自动查」的用例也就永远为真、测不出回归。
 *
 * 展开按钮用 `findByRole` 等出来而不是同步 `getByRole`：一轮 `await act` 只保证微任务
 * 队列被抽干，链路里但凡有一步走宏任务（定时器、rAF）就还没走完（实测：mock 改成
 * `setTimeout` 之后，一轮 act 结束时 `getCategories` 与 `searchAnalysis` 都还没被调用）。
 *
 * **但别把这里当成偶发失败的解药。** 那个按钮是无条件渲染的，不依赖任何请求落定——
 * 拿"所有请求永不 resolve"验证过，它照样在。真正让整批用例随机翻红的是模块级快照被
 * 迟到的响应写脏，修在 `useCachedState` 与 `index.tsx` 的 `persist` 里，回归见
 * `sessionCache.test.tsx`。
 */
export async function renderSettled(expandFilters = true) {
  await act(async () => {
    render(<AnalysisPage />);
  });
  // 只在要展开时才等这个按钮：复原路径（expandFilters=false）下快照连 filterOpen 一起
  // 带回来，过滤区已是展开态，按钮写的是「隐藏过滤条件」——无条件等它会等到超时。
  if (expandFilters) {
    fireEvent.click(await screen.findByRole('button', { name: /展开过滤条件/ }));
  }
}

/** 给"之后没有再发请求"这类断言一个观察窗口。 */
export async function settle(ms = 60) {
  await act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, ms);
    });
  });
}

export const submit = () => screen.getByRole('button', { name: /立即查询/ });
export const applyFilters = () => screen.getByRole('button', { name: /开始筛选/ });
