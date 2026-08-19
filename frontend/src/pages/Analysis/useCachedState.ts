import { useCallback, useEffect, useRef, useState } from 'react';
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

  /**
   * 组件是否还挂着。**卸载之后不再写快照。**
   *
   * 快照是模块级的、活过组件的一生，而 `set` 会被迟到的响应调用——用户切走时还在飞的
   * 请求落地后照样走到这里。少了这道闸，一次被放弃的请求会把它的结果写进快照，用户
   * 切回来看到的是自己已经离开的那次查询的状态。
   *
   * 在测试里同一个机制更凶：快照跨用例存活，上一个用例的迟到响应会在下一个用例的
   * `clearSnapshot()` **之后**把整份草稿写回来（实测一次 `getSites` 迟到就写回 7 个键）。
   * 下一个用例挂载时读到非空快照，于是走复原短路、一个请求都不发——表现是一批与改动
   * 毫无关系的用例随机翻红，且每次翻红的都不是同一批。
   *
   * 写成 effect 里置位而不是 `useRef(true)` 一次性赋值：StrictMode 下 setup → cleanup →
   * setup 会跑两轮，只在 cleanup 里置 false 会让第二轮之后永远写不进去。
   */
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const set = useCallback(
    (next: Updater<CachedValues[K]>) => {
      const resolved =
        typeof next === 'function'
          ? (next as (prev: CachedValues[K]) => CachedValues[K])(latest.current)
          : next;
      latest.current = resolved;
      if (!mounted.current) return;
      writeValue(key, resolved);
      setValue(resolved);
    },
    [key],
  );

  return [value, set];
}
