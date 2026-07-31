import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

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
