import { useState } from 'react';
import {
  getCategories,
  getScope,
  type CategoryOption,
  type ScopeInfo,
  type View,
} from '@/services/analysis';
import { getSites, type SiteOption } from '@/services/upload';
import type { ScopeDraft } from './filters';
import type { SavedMode } from './savedMode';
import { useCachedState } from './useCachedState';

/**
 * 分析查询屏的**作用域**这一层：站点、视图、日期，以及跟着它们走的选项清单
 * （站点清单、可用日期、类目下拉）。
 *
 * 边界是"**选项**从哪来、什么时候失效"，不含"用户勾了哪些"——后者是筛选条件，归
 * `useAnalysisQuery`。类目恰好在两边同名却生命周期相反（清单跟着作用域走，勾选跟着
 * 用户走），合进一个 hook 会让"这个 categories 是哪一个"重新要靠上下文判断，正是拆分
 * 要消除的东西。
 *
 * 因此本模块**不 import `useAnalysisQuery`**：作用域失效时要清掉的那份筛选，经
 * `onScopeInvalidated` 回调由页面接线（见 design 决策 1）。方向单向——作用域通知筛选，
 * 筛选永不反向影响作用域。
 */
export interface AnalysisScopeState {
  scope: ScopeDraft;
  setScope: (next: ScopeDraft | ((prev: ScopeDraft) => ScopeDraft)) => void;
  sites: SiteOption[];
  availableDates: string[];
  categories: CategoryOption[];
  /** 当前作用域一期数据都没有。空表格文案的判据，见下面的说明。 */
  scopeHasNoDates: boolean;
  loadSites: () => void;
  reloadCategories: (site: string | undefined, view: View, date: string | null | undefined) => void;
  reloadScopeMeta: (
    site: string | undefined,
    view: View,
    opts?: { keepCategories?: boolean; skipErrorHandler?: boolean },
  ) => Promise<ScopeInfo>;
  onScopeChange: (next: ScopeDraft) => void;
}

export function useAnalysisScope(options: {
  /** 保存的条件，只用来定草稿初值（站点与视图）。没有就落到内置默认。 */
  saved: SavedMode | null;
  /** 这次挂载是复原——复原路径不调 `reloadScopeMeta`，故 `scopeLoaded` 初值跟着它走。 */
  restored: boolean;
  /**
   * 作用域失效，请清掉与它绑定的筛选（类目与品牌）。
   * 由页面接到 `useAnalysisQuery` 的 `clearCategoryFilters` 上。
   */
  onScopeInvalidated: () => void;
}): AnalysisScopeState {
  const { saved, restored, onScopeInvalidated } = options;

  // 草稿态：用户正在编辑。初值优先用保存的条件，没有才落到内置默认（三级优先级的 2 与 3；
  // 第 1 级是快照，由 useCachedState 自己承担——有快照时下面这些初值根本不会被采用）。
  const [scope, setScope] = useCachedState(
    'scope',
    () => ({ site: saved?.site, view: saved?.view ?? 'DAY' }) as ScopeDraft,
  );

  const [sites, setSites] = useCachedState('sites', [] as SiteOption[]);
  const [availableDates, setAvailableDates] = useCachedState('availableDates', [] as string[]);
  const [categories, setCategories] = useCachedState('categories', [] as CategoryOption[]);

  /**
   * 作用域元数据是否已经取回。**不进缓存**：复原路径不调 `reloadScopeMeta`，但快照里
   * 的 `availableDates` 本就是取回过的，故初值直接跟着 `restored` 走。
   */
  const [scopeLoaded, setScopeLoaded] = useState(restored);

  /**
   * 当前作用域一期数据都没有。这是首屏自动查询之后「空表格」唯一还剩的非零行成因——
   * 没有日期可查，列表 `request` 开头那道 `!body?.date` 守卫会直接返回空、连请求都不发。
   *
   * 必须与「查出来是 0 行」分开：显示「搜索结果：0」会被读成条件把行筛没了，用户会去
   * 排查筛选条件，而真正的原因是这个站点根本没导过数据。
   *
   * `scopeLoaded` 这道前置是为了压掉挂载那一瞬——那时 `availableDates` 还是初值空数组，
   * 不加前置会先闪一下「还没有导入任何数据」再出数据，而那句话在当时是错的。
   */
  const scopeHasNoDates = scopeLoaded && availableDates.length === 0;

  const loadSites = () => {
    getSites()
      .then(setSites)
      .catch(() => undefined);
  };

  /**
   * 重拉类目选项。**日期也是选项口径的一部分**——统计按 (站点, 周期, 日期) 分片，
   * 与筛选严格同粒度，所以换日期同样要重拉，否则下拉里的计数属于另一个日期。
   *
   * 改造前统计是跨整个窗口聚合的，下拉说 21944、实际筛出来 17497，用户没法把这两个
   * 数字对上。现在两者同源，代价是换日期要多一次小表查询。
   */
  const reloadCategories = (
    nextSite: string | undefined,
    nextView: View,
    nextDate: string | null | undefined,
  ) => {
    getCategories(nextSite, nextView, nextDate)
      .then(setCategories)
      .catch(() => setCategories([]));
  };

  /**
   * 重拉作用域元数据。站点或视图变化时调用。
   *
   * 只更新选择器选项与类目清单，**不** reload 表格——查询由「立即查询」触发。
   * 已勾选的类目一并清空：上一个站点的类目在新站点下可能不存在，保留会产生一个
   * 查不出任何数据且原因不可见的条件。清哪些、怎么清由 `onScopeInvalidated` 承担
   * （草稿与生效两份都要清，理由见 `useAnalysisQuery.clearCategoryFilters`）。
   */
  const reloadScopeMeta = async (
    nextSite: string | undefined,
    nextView: View,
    opts?: { keepCategories?: boolean; skipErrorHandler?: boolean },
  ) => {
    const s = await getScope(nextSite, nextView, { skipErrorHandler: opts?.skipErrorHandler });
    setScopeLoaded(true);
    setAvailableDates(s.available_dates);
    setScope({ site: s.site, view: nextView, date: s.latest_date });
    /**
     * `keepCategories` 只有首屏按**保存的作用域**初始化时才为真。
     *
     * 清类目的理由是「作用域变了，旧站点的类目在新站点下可能不存在」——那说的是用户
     * 主动换站点。而首屏这次不是「变化」，作用域从一开始就是保存的那个，连同类目一起
     * 存的；照常清掉等于让「保存类目」这件事静默失效，用户存了却每次进屏都没有。
     */
    if (!opts?.keepCategories) {
      onScopeInvalidated();
    }
    reloadCategories(s.site, nextView, s.latest_date);
    return s;
  };

  const onScopeChange = (next: ScopeDraft) => {
    // 站点或视图变了：日期与类目清单跟着变，得重拉
    if (next.site !== scope.site || next.view !== scope.view) {
      reloadScopeMeta(next.site, next.view).catch(() => undefined);
      return;
    }
    // 只换了日期：日期清单不变，但类目计数是按日期分片的，要跟着换。
    // 已勾选的类目**不清**——同一站点同一视图下，类目在各日期之间基本是同一批，
    // 清掉会让用户每换一次日期就得重勾一遍。计数变了，选项本身仍然有效。
    if (next.date !== scope.date) {
      reloadCategories(next.site, next.view, next.date);
    }
    setScope(next);
  };

  return {
    scope,
    setScope,
    sites,
    availableDates,
    categories,
    scopeHasNoDates,
    loadSites,
    reloadCategories,
    reloadScopeMeta,
    onScopeChange,
  };
}
