import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { clearSnapshot, readSnapshot } from './stateCache';
import { useCachedState } from './useCachedState';

beforeEach(() => clearSnapshot());

describe('useCachedState', () => {
  it('没有缓存时用初值', () => {
    const { result } = renderHook(() => useCachedState('appliedSig', 'fallback'));
    expect(result.current[0]).toBe('fallback');
  });

  it('set 之后写进快照', () => {
    const { result } = renderHook(() => useCachedState('appliedSig', ''));
    act(() => result.current[1]('sig-1'));
    expect(result.current[0]).toBe('sig-1');
    expect(readSnapshot()?.values.appliedSig).toBe('sig-1');
  });

  it('重新挂载时从快照取初值，忽略传入的初值', () => {
    const first = renderHook(() => useCachedState('appliedSig', ''));
    act(() => first.result.current[1]('sig-1'));
    first.unmount();

    // 模拟切走再切回：全新实例，初值仍写 '' ，但应当拿到快照里的值
    const second = renderHook(() => useCachedState('appliedSig', ''));
    expect(second.result.current[0]).toBe('sig-1');
  });

  it('函数式更新基于最新值，同一 tick 内连续调用不丢更新', () => {
    const { result } = renderHook(() => useCachedState('resultTotal', 0));
    act(() => {
      result.current[1]((n) => n + 1);
      result.current[1]((n) => n + 1);
    });
    expect(result.current[0]).toBe(2);
    expect(readSnapshot()?.values.resultTotal).toBe(2);
  });

  it('惰性初值（函数形式）只在没有缓存时求值', () => {
    let calls = 0;
    const lazy = () => {
      calls += 1;
      return 'computed';
    };
    const first = renderHook(() => useCachedState('appliedSig', lazy));
    expect(first.result.current[0]).toBe('computed');
    expect(calls).toBe(1);

    act(() => first.result.current[1]('sig-1'));
    first.unmount();

    const second = renderHook(() => useCachedState('appliedSig', lazy));
    expect(second.result.current[0]).toBe('sig-1');
    expect(calls).toBe(1);
  });

  it('清空快照后回到初值', () => {
    const first = renderHook(() => useCachedState('appliedSig', 'fallback'));
    act(() => first.result.current[1]('sig-1'));
    first.unmount();
    clearSnapshot();

    const second = renderHook(() => useCachedState('appliedSig', 'fallback'));
    expect(second.result.current[0]).toBe('fallback');
  });
});
