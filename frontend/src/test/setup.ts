import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

/**
 * 抬高 `findBy*` / `waitFor` 的等待上限（默认 1000ms）。
 *
 * **这不是在放宽断言**——等的是同一件事，只是允许它在负载下慢一点到。
 *
 * 全量跑 27 个 jsdom + antd 文件时 vitest 按核心数并行，单个重文件（分析查询屏 48 个
 * 用例）要跑 45 秒以上。平时 200ms 渲染完的表格在这种争抢下轻易超过 1 秒，于是
 * `findByText` 超时——本地单独跑那个文件永远是绿的，表现为随机拦下提交门禁。
 *
 * **范围要说清楚**：这只覆盖走 `findBy*` / `waitFor` 的用例。同一批偶发失败里还有
 * 另一类与它无关——`renderSettled` 那种只 `await` 一个 act 周期、随后用同步
 * `getByRole` 断言的写法，等的不是某个条件成立，负载下首屏请求还没落定就断言了。
 * 那一类要改测试本身，不是调这里的超时。
 */
configure({ asyncUtilTimeout: 5000 });

// antd 的响应式栅格与 ProComponents 会读 matchMedia，jsdom 没有实现，必须补。
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

// ProTable 的虚拟滚动与 antd 的尺寸感知组件依赖 ResizeObserver，jsdom 同样没有。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = window.ResizeObserver ?? (ResizeObserverStub as never);

// Ant Design X 的 Bubble.List（滚动兼容）用 IntersectionObserver，jsdom 没有实现；
// 补一个什么都不触发的桩即可（change adopt-antdx-opportunity-ui）。
class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
window.IntersectionObserver = window.IntersectionObserver ?? (IntersectionObserverStub as never);

/**
 * 只过滤 act(...) 这一类警告。
 *
 * 来源是 ProTable/antd 内部的异步生命周期（首次 request 落定、Button loading 翻转），
 * 不是被测代码的问题，用 act 包裹渲染也压不住。一个用例会刷十几条，真实失败会被淹没。
 *
 * 代价要说清楚：act 警告只反映"测试里有未被 await 的状态更新"，不反映生产缺陷；
 * 本文件其余 console.error（PropTypes、React key、抛错边界等）一律照常输出。
 */
const originalError = console.error;
console.error = (...args: unknown[]) => {
  if (typeof args[0] === 'string' && args[0].includes('not wrapped in act(')) return;
  originalError(...args);
};

// 每个用例后清 DOM 与 localStorage，避免 token 残留影响下一个用例
afterEach(() => {
  cleanup();
  localStorage.clear();
});
