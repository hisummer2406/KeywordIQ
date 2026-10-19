import type { ActionType } from '@ant-design/pro-components';
import { message } from 'antd';
import { useMemo, useRef, useState, type MutableRefObject } from 'react';
import {
  exportAnalysis,
  searchAnalysis,
  type AnalysisRow,
  type SearchBody,
  type View,
} from '@/services/analysis';
import {
  defaultFilters,
  sanitizeCompetitorNew,
  toSearchBody,
  type FilterDraft,
  type ScopeDraft,
} from './filters';
import { clearSavedMode, writeSavedMode, type SavedMode } from './savedMode';
import type { RequestKey } from './stateCache';
import type { AnalysisSession } from './useAnalysisSession';
import { useCachedState } from './useCachedState';

/**
 * 分析查询屏的**查询**这一层：草稿态与生效态两份条件、排序、分页总数缓存、导出，
 * 以及结果表格那次请求本身。
 *
 * **草稿态与生效态是两份状态**，不是一份加脏标记。一旦条件与表格数据来自同一份状态，
 * 表格重渲染时就会读到尚未生效的条件，「当前显示的是哪组条件」无法从状态里读出来，
 * 导出（复用最近一次生效的查询体）也会跟着漂移。
 *
 * 作用域三元组由 `useAnalysisScope` 持有，这里只**读**它去组装 `SearchBody`——收窄到
 * 值而不是收整个 hook，"查询会不会偷偷去重拉作用域"这个问题就不用问了（design 决策 1）。
 */

/** SearchBody 里属于「筛选」的字段。行集合开关（include）不在其中——它是行集合维度。 */
const FILTER_KEYS = [
  'keyword', 'asin', 'product_title', 'brands', 'categories',
  'ranking_min', 'ranking_max', 'change_min', 'change_max',
  'click_share_min', 'click_share_max', 'conversion_share_min', 'conversion_share_max',
  'is_new', 'competitor_new',
] as const satisfies readonly (keyof SearchBody)[];

type SortDraft = { field: string; dir: 'asc' | 'desc' };

export interface AnalysisQueryState {
  filters: FilterDraft;
  setFilters: (next: FilterDraft | ((prev: FilterDraft) => FilterDraft)) => void;
  sortDraft: SortDraft;
  setSortDraft: (next: SortDraft | ((prev: SortDraft) => SortDraft)) => void;
  /** 生效条件。JSX 里按 `appliedRef.current?.site` 判按钮禁用与词库归属，故直接给出 ref。 */
  appliedRef: MutableRefObject<SearchBody | null>;
  appliedView: View;
  appliedSig: string;
  /** 表格里这批行实际所属的站点/视图/日期，详情抽屉专用——理由见同名 state 定义处。 */
  loadedSite: string | undefined;
  loadedView: View | undefined;
  loadedDate: string | null | undefined;
  actionRef: MutableRefObject<ActionType | undefined>;
  resultTotal: number;
  pendingRebuild: boolean;
  exporting: boolean;
  savedApplied: boolean;
  setSavedApplied: (next: boolean) => void;
  /** 已经查过一次。结果条数的显隐与「有未生效改动」提示的前置。 */
  hasQueried: boolean;
  /**
   * 最近一次（最新发出的）查询失败了，尚未有成功的查询把它盖掉。
   * 此时表格里要么是空的、要么还是**上一组条件**的行，都不是当前生效条件的结果，
   * 界面必须明示，且不得显示结果条数。
   */
  requestFailed: boolean;
  /** 草稿与生效不一致。**没查过就恒为假**，见下面的说明。 */
  dirty: boolean;
  /** 生效条件带了筛选 ⇒ 掉榜段被短路为空，界面须明示。 */
  appliedHidesDropped: boolean;
  draftBody: SearchBody;
  apply: (body: SearchBody) => void;
  submitScope: () => void;
  submitFilters: () => void;
  resetFilters: () => void;
  saveCurrentMode: () => void;
  applySort: () => void;
  onExport: () => Promise<void>;
  /** 作用域失效时清掉与它绑定的筛选。接给 `useAnalysisScope` 的 `onScopeInvalidated`。 */
  clearCategoryFilters: () => void;
  runRequest: (params: { current?: number; pageSize?: number }) => Promise<{
    data: AnalysisRow[];
    total: number;
    success: boolean;
  }>;
}

export function useAnalysisQuery(options: {
  /** 作用域草稿，只读。组装 `SearchBody` 用。 */
  scope: ScopeDraft;
  session: AnalysisSession;
  saved: SavedMode | null;
  userId: number | undefined;
  canViewCompetitor: boolean;
}): AnalysisQueryState {
  const { scope, session, saved, userId, canViewCompetitor } = options;
  const { initialSnapshot, persist, markDataReady, consumeRestoredRows } = session;

  const actionRef = useRef<ActionType>();
  /**
   * `runRequest` 的调用序号。ProTable 允许同一时刻有多个 `request` 调用同时在飞——
   * 切条件时旧请求不会被真的取消（`useFetchData` 的 `abortFetch` 只掐断它自己内部
   * 的 debounce race，不掐断已经发出的 `getData` promise 本身），两次调用可能乱序
   * 落地。若较早发出的那次后落地，仍然覆盖 `loadedSite`/`loadedView`/`loadedDate`/
   * 总数缓存，表格已经在显示较新请求的行，详情抽屉与总数却回退到旧作用域
   * （PR #87 第五轮 P2）。
   */
  const requestSeqRef = useRef(0);

  /** 本次进屏是否确实按保存的条件初始化了——「我的默认」标记的判据。 */
  const [savedApplied, setSavedApplied] = useState(saved !== null);

  const [filters, setFilters] = useCachedState('filters', () =>
    saved
      ? sanitizeCompetitorNew({ ...saved.filters, q: '', qMode: 'ALL_TERMS' }, canViewCompetitor)
      : defaultFilters(),
  );

  // 生效态：表格 request 实际读的那份。用 ref 承载，供 request 闭包读取。
  const appliedRef = useRef<SearchBody | null>(initialSnapshot?.applied ?? null);
  // 生效态的视图单独提出来做 state：列集合（走势/周变化）随它变，需要触发重渲染
  const [appliedView, setAppliedView] = useCachedState('appliedView', 'DAY' as View);
  // 生效条件的签名，与草稿签名比较得出 dirty，不额外维护脏标记
  const [appliedSig, setAppliedSig] = useCachedState('appliedSig', '');

  /**
   * 表格里这批行实际所属的站点/视图/日期，取自最近一次成功响应（`SearchResponse.
   * site/view/date`，复原分支取的是与那批缓存行匹配的请求体，效果同源）——不能用
   * `appliedRef.current`/`appliedView`：`apply()` 把它们同步改到新作用域，随后才
   * `reload(true)` 发一轮网络往返，这段窗口内表格显示的仍是旧作用域那批行。详情抽屉
   * 若跟着新 `appliedRef`/`appliedView` 走，会把仍在渲染的旧行的 keyword_id 配上新
   * 站点/视图/日期，要么触发后端跨站点校验（422），要么把一份数据按错误的日期语境
   * 解析（PR #87 复审 P2，与 KeywordLibrary 的 loadedSite/loadedView/loadedDate 同一
   * 模式）。不进会话快照——与 `trendTarget` 同理，切走再切回清空是预期行为，且 ProTable
   * 挂载即调 `runRequest`（复原分支同样经过它），首次渲染前就会补齐。
   */
  const [loadedSite, setLoadedSite] = useState<string | undefined>();
  const [loadedView, setLoadedView] = useState<View | undefined>();
  const [loadedDate, setLoadedDate] = useState<string | null | undefined>();

  const [resultTotal, setResultTotal] = useCachedState('resultTotal', 0);
  // 总数缓存与"下次要重算"的标记。ProTable 的 request 同时承担翻页与条件变更两种触发，
  // 靠这个标记把两者区分开——只有条件变更那次才让后端付精确计数的代价。
  const totalRef = useRef(initialSnapshot?.total ?? 0);
  const needsTotalRef = useRef(initialSnapshot?.needsTotal ?? true);
  const [pendingRebuild, setPendingRebuild] = useCachedState('pendingRebuild', false);
  const [sortDraft, setSortDraft] = useCachedState(
    'sortDraft',
    () => saved?.sort ?? { field: 'current_ranking', dir: 'asc' as 'asc' | 'desc' },
  );
  const appliedSortRef = useRef(initialSnapshot?.appliedSort ?? sortDraft);

  // 导出中的状态**不进缓存**：它描述的是一次正在进行的请求，而请求随组件一起没了。
  // 缓存它会让用户切回来看到一个永远转下去的按钮。
  const [exporting, setExporting] = useState(false);

  /**
   * 是否已经查过一次。`appliedSig` 只在 `apply()` 里被写非空值，所以它就是这个判据，
   * 不用再养一个独立标记。
   *
   * 首屏自动查询之后，它只剩两个用途：**结果条数的显隐**与「有未生效改动」提示的前置。
   * 已经不再是空表格文案的判据——那件事交给 `scopeHasNoDates`（在 `useAnalysisScope`）。
   *
   * 条数仍然跟着它而不是跟着 `scopeHasNoDates`：用户切到一个没有数据的站点时表格**不**
   * 重查（这是既有约定），屏上仍是上一个站点的行，那时条数必须继续显示，因为它描述的
   * 正是那批还在屏上的行。
   */
  const hasQueried = appliedSig !== '';

  /**
   * 最近一次查询是否失败（issue #92：断网/服务不可达时不得把失败显示成空数据）。
   *
   * 为什么需要它：`hasQueried` 在 `apply()` 里、请求发出**之前**就变真了，而失败的请求
   * 不写任何结果状态。于是首次查询就失败时，界面是「搜索结果：0」+ 空表格——读起来是
   * 「这个条件没有命中」，实际是「没查成」；后续查询失败时则是表格仍留着上一组条件的行、
   * 而「有未生效改动」不亮（草稿 === 生效），等于在说这就是新条件的结果。toast 几秒就消失，
   * 撑不住这两种误读，所以要一个常驻的状态。
   *
   * **不进会话快照**：它描述的是一次请求的结局，请求随组件一起没了；缓存它会让用户切回来
   * 看到一个已经没有意义的失败提示（与 `exporting` 同理）。
   */
  const [requestFailed, setRequestFailed] = useState(false);

  const draftBody = useMemo(() => toSearchBody(scope, filters), [scope, filters]);
  const draftSig = useMemo(() => JSON.stringify(draftBody), [draftBody]);
  /**
   * 「有未生效改动」提示。**没查过就不提示**——那时生效态是空的，判据必然为真，而用户
   * 一个条件都没改，「存在未生效条件」这句话是错的。首屏自动查询之后这一刻通常只是
   * 作用域还没取回的那一瞬，但站点无数据时会一直停在这个状态，前置不能去掉。
   */
  const dirty = hasQueried && draftSig !== appliedSig;

  // 生效条件里是否带了任何筛选（行集合开关不算——它是行集合维度，不是筛选）。
  // 带了就意味着掉榜段被短路为空，界面须明示。
  const appliedHidesDropped = (() => {
    const b = appliedRef.current;
    if (!b || b.include === 'DROPPED' || b.include === 'LISTED') return false;
    // 显式列出筛选字段，不用"排除已知非筛选字段"的写法：后者在 SearchBody 新增任何
    // 字段时都会把它当成筛选，从而误报。
    return FILTER_KEYS.some((k) => b[k] !== undefined && b[k] !== null);
  })();

  /**
   * 把当前草稿设为生效条件并重查（回第 1 页）。
   *
   * 顺带把"下次请求要重算总数"立起来。总数是精确计数，代价与命中行数同阶（周报最坏
   * 约 0.9s），而它**只随条件变化**——翻页、改排序、改每页条数都不会改变命中了多少行。
   * 所以只有这里（条件真的变了）才需要重算，其余情况沿用缓存值。
   *
   * **`competitor_new` 在这里再做一次权限兜底**（PR #66 复审 P2）：草稿状态已经在
   * 读取保存的条件时按权限规范化过（见 `sanitizeCompetitorNew`），这里是三处生效入口
   * （立即查询/开始筛选/首屏自动查询）唯一共同必经之处，兜底不依赖调用方各自记得。
   * `sig` 由本函数统一重算而不是接调用方传入的——否则去掉字段后 `body` 与传入的
   * `sig` 不再对应同一份内容，`draftSig`/`appliedSig` 的比较会失真。
   */
  const apply = (body: SearchBody) => {
    const safeBody: SearchBody = canViewCompetitor ? body : { ...body, competitor_new: undefined };
    appliedRef.current = safeBody;
    needsTotalRef.current = true;
    persist({ applied: safeBody, needsTotal: true });
    setAppliedSig(JSON.stringify(safeBody));
    setAppliedView(safeBody.view);
    actionRef.current?.reload(true);
  };

  /**
   * 作用域栏的「立即查询」：只把作用域三元组与检索词设为生效，筛选沿用已生效那份。
   *
   * `scopeOnly` 里的筛选值通常被下面 `...appliedRef.current` 整个盖掉，**唯独
   * `appliedRef.current` 为 null 时（还没查过任何一次就直接点这里）会被整体采用**。
   * 那种情况下它带的正是 `defaultFilters()` 的默认筛选，也正是想要的行为——所以这里
   * 并非与默认值无关的调用点，改默认值时这条路径跟着变。
   */
  const submitScope = () => {
    const scopeOnly = toSearchBody(scope, { ...defaultFilters(), q: filters.q, qMode: filters.qMode });
    const body: SearchBody = {
      ...(appliedRef.current ?? scopeOnly),
      site: scopeOnly.site,
      view: scopeOnly.view,
      date: scopeOnly.date,
      keyword: scopeOnly.keyword,
      keyword_mode: scopeOnly.keyword_mode,
      asin: scopeOnly.asin,
    };
    apply(body);
  };

  const submitFilters = () => {
    const current = appliedRef.current ?? draftBody;
    const body: SearchBody = {
      ...draftBody,
      site: current.site,
      view: current.view,
      date: current.date,
      keyword: current.keyword,
      keyword_mode: current.keyword_mode,
      asin: current.asin,
    };
    apply(body);
  };

  /**
   * 「重置」清成**默认**（含只看新词），不是清成全不筛。界面因此没有一键回到「全部词」
   * 的入口——那需要第二个按钮「恢复默认」，与「重置」语义相近，用户得先分辨差别才能
   * 选对，正是「一个筛选维度恰好一个入口」要避免的形态。想看全量榜单就把新词开关拨到
   * 「全部」，多一次点击。
   *
   * `q` 保留：重置的是过滤区，作用域栏的检索框不归它管。
   *
   * **连带删除用户保存的默认条件**，使下次登录回到内置默认——否则用户重置完，下次登录
   * 又会回到那组他刚刚明确不要的条件。按需求方要求**不做二次确认**；已知代价是这个删除
   * 不可撤销，而这个按钮看起来只是改改表单。
   *
   * 只改筛选，**不动站点/视图/排序**：它长在过滤区里，去改作用域栏的东西很意外。所以
   * 重置后界面上站点仍是保存过的那个，但存储里那条已经没了。
   */
  const resetFilters = () => {
    setFilters((current) => ({ ...defaultFilters(), q: current.q, qMode: current.qMode }));
    clearSavedMode(userId);
    setSavedApplied(false);
  };

  /**
   * 作用域变了，清掉与它绑定的筛选。由 `useAnalysisScope` 经回调调用。
   *
   * **草稿与生效两份都要清。** 类目清单是按 (站点, 视图) 取的，作用域一变两边的类目就
   * 同时作废。只清草稿的话，`submitScope` 会把生效态里那份旧作用域的类目原样带进下一次
   * 查询——界面上一个勾都没有，表格却查不出数据，正是这里要避免的那种不可见条件。
   *
   * brands 一并清：品牌筛选面板暂时下线，但字段仍在模型里，留着不清会成为一个界面上
   * 看不见却在生效的条件。
   */
  const clearCategoryFilters = () => {
    setFilters((f) => ({ ...f, categories: [], brands: [] }));
    if (appliedRef.current) {
      appliedRef.current = { ...appliedRef.current, categories: undefined, brands: undefined };
      persist({ applied: appliedRef.current });
    }
  };

  /**
   * 把**当下生效的**条件存为「我的默认」。
   *
   * 存的必须是用户**已经看到过结果**的那组，不能是草稿：草稿改了没点「开始筛选」就保存，
   * 存下的是一组他自己都没见过结果的条件，下次登录进屏看到的东西他不认识。
   *
   * 实现上不另存一份「生效时的 FilterDraft」，而是**在 `dirty` 时拒绝保存**——`draftSig`
   * 由 `toSearchBody(scope, filters)` 得出，作用域与筛选都在里面，所以 `dirty` 为假就
   * 等价于「草稿 === 生效」，此刻的 `filters` 就是生效的那份。这样既不必新增状态，也不必
   * 动会话快照的形状（那要 `CACHE_VERSION` +1）。
   *
   * 不存日期（永远取最新一期）、不存检索词（一次性查找不是「常看的模式」），见 savedMode.ts。
   */
  const saveCurrentMode = () => {
    const applied = appliedRef.current;
    // 站点为空就无从复原作用域，不存半份。按钮此时已禁用，这里是双保险
    if (!applied?.site) return;
    if (dirty) {
      message.warning('有未生效的条件，请先点「开始筛选」，再保存你看到的这组');
      return;
    }
    // q 与 qMode 不入库：检索词是一次性查找（模式只修饰检索词），不是「常看的模式」。解构剥掉它，`void` 是为了让
    // lint 看到这个变量确实被「用」了（本项目没开 ignoreRestSiblings）
    const { q, qMode, ...savableFilters } = filters;
    void q;
    void qMode;
    const ok = writeSavedMode(userId, {
      site: applied.site,
      view: applied.view,
      filters: savableFilters,
      sort: appliedSortRef.current,
    });
    if (ok) {
      setSavedApplied(true);
      message.success('已保存为你的默认条件，下次登录进来就是这组');
    } else {
      message.error('保存失败：浏览器不允许写入本地存储');
    }
  };

  /** 工具栏「确定」：把草稿排序设为生效并重查。排序不改变命中行数，故不重算总数。 */
  const applySort = () => {
    appliedSortRef.current = sortDraft;
    persist({ appliedSort: sortDraft });
    actionRef.current?.reload(true);
  };

  const onExport = async () => {
    const body = appliedRef.current;
    if (!body) return;
    setExporting(true);
    try {
      // 导的就是用户当下看到的那份：生效条件 + 生效排序，不含分页。
      // 排序必须一起带上——行的集合一样但顺序不同的 CSV，用户对不上屏幕上看到的东西。
      await exportAnalysis({
        ...body,
        orderBy: appliedSortRef.current.field,
        orderDir: appliedSortRef.current.dir,
      });
    } catch (e: any) {
      // 413（超出行数上限）在这里以后端原文呈现，提示用户加筛选而不是给一份截断数据
      message.error(e?.message || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const runRequest = async (params: { current?: number; pageSize?: number }) => {
    const body = appliedRef.current;
    if (!body?.date) return { data: [] as AnalysisRow[], total: 0, success: true };
    // 序号在函数入口就占位（早于任何 await），保证同一时刻发出的多次调用各自持有
    // 严格递增、互不相同的号——谁的响应最后落地都能准确判断自己是否已经过期。
    const seq = (requestSeqRef.current += 1);
    const key: RequestKey = {
      body,
      orderBy: appliedSortRef.current.field,
      orderDir: appliedSortRef.current.dir,
      page: params.current ?? 1,
      perPage: params.pageSize ?? 50,
    };
    // 复原后的第一次请求可能直接拿快照里的行交差（一次性，且核对完整请求标识）。
    // 语义与理由收在 `useAnalysisSession.consumeRestoredRows` 里。
    const restoredRows = consumeRestoredRows(key);
    if (restoredRows) {
      // 这批行就是按 body 这份请求体查出来的（`consumeRestoredRows` 核对过请求标识），
      // 与真实请求分支同一处理：详情抽屉必须跟着这批行走，不是跟着可能已经变了的 appliedRef。
      // 这段全程没有 await，天然是本次调用里最先落地的一段——但仍要核对 seq：更早
      // 发出、此刻仍在网络往返中的旧调用可能在它之后才 resolve，不能被旧调用倒灌覆盖。
      if (seq === requestSeqRef.current) {
        setRequestFailed(false);
        setLoadedSite(body.site);
        setLoadedView(body.view);
        setLoadedDate(body.date ?? null);
      }
      return { ...restoredRows, success: true };
    }

    // 排序只有工具栏一个入口（列上没有 sorter），故不读 ProTable 的 sort 参数——
    // 读了也永远是空对象，那种分支看着像在工作，实际是死代码。
    // 只有条件变更那次要重算总数；翻页/改排序沿用缓存值（见 apply）。
    // 标记在**拿到成功响应之后**才清——请求失败时保持置位，重试仍会带上重算，
    // 否则一次失败会让这组条件永远拿不到总数。
    const wantTotal = needsTotalRef.current;
    // try 覆盖到函数末尾，不只是 `searchAnalysis` 这一句：ProTable 在 `onRequestError` 存在时
    // 会吞掉 request 里抛出的**一切**异常（不只是网络失败），响应处理段里的 bug 若落在 try
    // 之外，就是「界面显示旧条数 + 旧行 + 没有任何输出」的静默失败。
    try {
      const res = await searchAnalysis({
        ...body,
        orderBy: appliedSortRef.current.field,
        orderDir: appliedSortRef.current.dir,
        page: params.current,
        perPage: params.pageSize,
        include_total: wantTotal,
      });
      if (wantTotal) needsTotalRef.current = false;
      // total 为 null 表示"本次没算"，沿用缓存；0 是真的没有命中行，必须照收
      const total = res.total ?? totalRef.current;
      const rows = res.data ?? [];
      // 这次 await 期间已经有更晚的调用发出（seq 被推进），说明这次响应对应的作用域
      // 已经过期：ProTable 不会真的取消旧请求（见上方 requestSeqRef 的注释），继续把
      // loadedSite/View/Date、总数缓存与会话快照写成这份旧数据，会让它们与表格当下
      // 实际展示的（更新请求的）行对不上。仍然把这次查到的行还给 ProTable 自己的
      // 那次调用——它是否采纳属于 ProTable 内部的呈现逻辑，不是这里要接管的范围。
      if (seq !== requestSeqRef.current) {
        return { data: rows, total, success: res.success !== false };
      }
      totalRef.current = total;
      setResultTotal(total);
      setRequestFailed(false);
      setPendingRebuild(res.pending_rebuild === true);
      // 详情抽屉跟着这批行实际所属的站点/视图/日期走，见上方 loadedSite 的注释；
      // 三者取自响应本身，不是发请求时的闭包值。
      setLoadedSite(res.site);
      setLoadedView(res.view);
      setLoadedDate(res.date);
      // 把「这次请求是什么 + 拿回了什么」一并落进快照，供下次挂载短路
      persist({ lastRequest: key, rows, total, needsTotal: needsTotalRef.current });
      markDataReady();
      return { data: rows, total, success: res.success !== false };
    } catch (e) {
      // 网络/服务端失败的提示由全局 errorHandler 负责（这个调用没有 skipErrorHandler）；
      // 这里只记一笔结局，好让界面不再把这份表格当成当前条件的结果。**只认最新发出的那次**：
      // 更早发出的旧请求晚失败，不该把已经成功的新结果标成失败。异常照常抛回给 ProTable。
      if (seq === requestSeqRef.current) setRequestFailed(true);
      throw e;
    }
  };

  return {
    filters,
    setFilters,
    sortDraft,
    setSortDraft,
    appliedRef,
    appliedView,
    appliedSig,
    loadedSite,
    loadedView,
    loadedDate,
    actionRef,
    resultTotal,
    pendingRebuild,
    exporting,
    savedApplied,
    setSavedApplied,
    hasQueried,
    requestFailed,
    dirty,
    appliedHidesDropped,
    draftBody,
    apply,
    submitScope,
    submitFilters,
    resetFilters,
    saveCurrentMode,
    applySort,
    onExport,
    clearCategoryFilters,
    runRequest,
  };
}
