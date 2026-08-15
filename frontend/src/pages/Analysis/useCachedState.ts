import { useCallback, useRef, useState } from 'react';
import { readSnapshot, writeValue, type CacheKey, type CachedValues } from './stateCache';

type Updater<T> = T | ((prev: T) => T);

/**
 * 与 `useState` 同形，但初值取自会话缓存、每次 `set` 都 write-through 写回缓存。
 *
 * 组件被路由卸载再重挂载时（切走再切回），初值从快照读回，用户的条件因此不丢。
 * 传入的 `initial` 只在**没有缓存**时使用——首次进入或刷新页面之后。
 *
 * **写回放在 `set` 里、不在 `useState` 的更新函数里**：更新函数在渲染阶段执行，
 * 在里面做副作用会随 React 的重复调用而重复执行。这里用 ref 镜像最新值，
 * 函数式更新照常基于最新值求解，同一 tick 内连续调用也不丢更新。
 */
export function useCachedState<K extends CacheKey>(
  key: K,
  initial: CachedValues[K] | (() => CachedValues[K]),
): [CachedValues[K], (next: Updater<CachedValues[K]>) => void] {
  const [value, setValue] = useState<CachedValues[K]>(() => {
    const cached = readSnapshot()?.values[key];
    if (cached !== undefined) return cached as CachedValues[K];
    return typeof initial === 'function'
      ? (initial as () => CachedValues[K])()
      : initial;
  });

  const latest = useRef(value);
  latest.current = value;

  const set = useCallback(
    (next: Updater<CachedValues[K]>) => {
      const resolved =
        typeof next === 'function'
          ? (next as (prev: CachedValues[K]) => CachedValues[K])(latest.current)
          : next;
      latest.current = resolved;
      writeValue(key, resolved);
      setValue(resolved);
    },
    [key],
  );

  return [value, set];
}
