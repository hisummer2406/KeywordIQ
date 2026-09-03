import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnalysisRow } from '@/services/analysis';
import {
  readSnapshot,
  sameRequest,
  writeSlot,
  type AnalysisSnapshot,
  type RequestKey,
} from './stateCache';

/**
 * 分析查询屏的**会话**这一层：页面快照、复原短路、滚动位置的持久化与恢复。
 *
 * 这三件事共享同一个前提——快照是**模块级**的，活得比组件长。组件卸载之后仍然可能有
 * 迟到的响应回来调 `persist`，而快照非空本身就会让下次挂载走复原短路，所以"卸载后不再
 * 写"这道闸是三者共同的地基，不能分开放。
 *
 * 不认识站点、筛选、查询体——那些是 `useAnalysisScope` / `useAnalysisQuery` 的事。
 * 本模块也**不 import 它们**：依赖方向是单向的（见 openspec 变更 split-analysis-page-state
 * 的 design 决策 1），页面负责接线。
 */
export interface AnalysisSession {
  /** 挂载时读到的快照。整个生命周期只读这一次，各 ref 的初值都从它来。 */
  initialSnapshot: AnalysisSnapshot | null;
  /** 这次挂载是「复原」还是「首次进入」——挂载副作用那道闸的判据。 */
  restored: boolean;
  /** 写快照。卸载后是空操作，见下面 `mounted` 的说明。 */
  persist: (patch: Parameters<typeof writeSlot>[0]) => void;
  /** 结果行已交付，可以尝试恢复滚动位置了。真实取数与复原短路两条路径都要调。 */
  markDataReady: () => void;
  /**
   * 复原后的第一次请求：命中缓存就把行还回去，否则返回 `null` 让调用方真的去查。
   * **一次性**，第二次起恒为 `null`。
   */
  consumeRestoredRows: (key: RequestKey) => { data: AnalysisRow[]; total: number } | null;
}

export function useAnalysisSession(): AnalysisSession {
  /**
   * 挂载时读一次快照。它同时决定两件事：各 ref 的初值，以及**这次是「复原」还是
   * 「首次进入」**——后者是挂载副作用那道闸的判据。用惰性 useState 而不是直接调用，
   * 保证整个生命周期只读一次；之后所有写入都走 write-through。
   */
  const [initialSnapshot] = useState(() => readSnapshot());
  const restored = initialSnapshot !== null;

  /**
   * 组件是否还挂着，以及**卸载后不再写快照**的那道闸。
   *
   * 与 `useCachedState` 里那道同源同因：快照是模块级的、活过组件一生，而各处
   * `persist` 都可能被迟到的响应调用（最典型的是列表 `request` 里那次——用户切走时
   * 请求还在飞）。放任它写，用户切回来看到的是自己已经离开的那次查询的结果。
   *
   * **一律走 `persist`，不要直接调 `writeSlot`。** 快照非空这一件事本身就会让下次挂载
   * 走复原短路、整段跳过初始化，所以漏掉任何一处的代价不是"少存了一个字段"，而是
   * "凭空多出一次复原"。
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
  const persist = useCallback((patch: Parameters<typeof writeSlot>[0]) => {
    if (!mounted.current) return;
    writeSlot(patch);
  }, []);

  /**
   * 滚动位置的恢复。
   *
   * **必须等结果行落到 DOM 之后**：行还没渲染时页面高度不足，`scrollTo` 会被截断到
   * 当前最大滚动量，停在一个与离开前对不上的位置——而这种错误没有任何地方会报错。
   * 故由列表 `request` 交付数据后调 `markDataReady`，再在 effect 里滚。
   *
   * **只恢复一次**：不清标记的话，用户在本屏内自己滚动、触发任一次重渲染都会被弹回。
   */
  const pendingScrollRef = useRef<number | null>(restored ? (initialSnapshot?.scrollY ?? 0) : null);
  const [dataTick, setDataTick] = useState(0);
  const markDataReady = useCallback(() => setDataTick((n) => n + 1), []);

  useEffect(() => {
    if (dataTick === 0) return;
    const y = pendingScrollRef.current;
    if (y === null || y === 0) return;
    pendingScrollRef.current = null;

    // `markDataReady` 在 request 返回**之前**调用，那时行还没进 DOM、页面高度不够，
    // 直接滚会被浏览器截断到当前最大滚动量（实测就是回到 0）。所以逐帧等页面长到
    // 够高再滚，并给一个上限——数据真的只有半屏时不能无限等下去。
    let frame = 0;
    let handle = 0;
    const tryScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      if (max >= y || frame > 30) {
        window.scrollTo({ top: y });
        return;
      }
      frame += 1;
      handle = requestAnimationFrame(tryScroll);
    };
    handle = requestAnimationFrame(tryScroll);
    return () => cancelAnimationFrame(handle);
  }, [dataTick]);

  /**
   * 持续把滚动量写进快照。节流到每帧一次——scroll 事件的频率远高于渲染帧。
   *
   * **不要改成「在 cleanup 里读一次 window.scrollY」**：那看起来更省事，实际是把
   * 正确值毁掉的路径。导航时新页面比当前页矮，浏览器会把滚动量夹到 0，而这个夹取
   * 与组件卸载谁先谁后并无保证——一旦排在前面，cleanup 存进去的就是 0，功能静默失效。
   * 监听器记的是用户滚动那一刻的值，之后任何重置都影响不到它。
   */
  useEffect(() => {
    let queued = false;
    const onScroll = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        persist({ scrollY: window.scrollY });
        queued = false;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [persist]);

  /** 复原的缓存行是否已经交付过。一次性——之后所有请求都真的去查。 */
  const restoreServedRef = useRef(false);

  /**
   * 短路只作用于**复原后的第一次请求**（切回来那一次），一次性。
   *
   * 不能改成「参数相同就用缓存」：那样同参数的「立即查询」也会被短路，而它是用户强制
   * 刷新的唯一入口——数据刚导完想看新的，点了却什么都不动。翻页、改排序、重新查询一律
   * 真的去查。
   *
   * 仍然要 `sameRequest` 核对：万一表格请求的页码/排序与缓存那次对不上（如页码没复原
   * 成功），拿缓存行去填就是显示了另一页的数据。
   *
   * 这四步（一次性闸、读快照、核对、`markDataReady`）**必须一起改**，所以收在一处：
   * 闸漏了每次翻页都吃缓存，核对漏了填进另一页的数据，`markDataReady` 漏了滚动位置
   * 恢复不到——分开写时它们中间隔着与短路无关的代码，读的人不容易看出是一套。
   */
  const consumeRestoredRows = useCallback(
    (key: RequestKey): { data: AnalysisRow[]; total: number } | null => {
      if (!restored || restoreServedRef.current) return null;
      restoreServedRef.current = true;
      const cached = readSnapshot();
      if (cached && sameRequest(key, cached.lastRequest)) {
        markDataReady();
        return { data: cached.rows, total: cached.total };
      }
      return null;
    },
    [restored, markDataReady],
  );

  return { initialSnapshot, restored, persist, markDataReady, consumeRestoredRows };
}
