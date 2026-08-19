import {
  DownOutlined,
  DownloadOutlined,
  FilterOutlined,
  ReloadOutlined,
  SaveOutlined,
  StarOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { useAccess, useModel } from '@umijs/max';
import { Alert, Button, Card, Col, Row, Select, Space, Tooltip, Typography, message } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  exportAnalysis,
  getCategories,
  getScope,
  searchAnalysis,
  type AnalysisRow,
  type CategoryOption,
  type SearchBody,
  type View,
} from '@/services/analysis';
import { getSites, type SiteOption } from '@/services/upload';
import AddToLibraryModal from './AddToLibraryModal';
import CategoryPanel from './CategoryPanel';
import FilterPanel, { LABEL_WIDTH } from './FilterPanel';
import ScopeBar from './ScopeBar';
import { buildColumns, totalColumnWidth } from './columns';
import { defaultFilters, toSearchBody, type ScopeDraft } from './filters';
import { clearSavedMode, readSavedMode, writeSavedMode } from './savedMode';
import { readSnapshot, sameRequest, writeSlot, type RequestKey } from './stateCache';
import { useCachedState } from './useCachedState';

/**
 * 分析查询屏：作用域 / 过滤 / 结果三段。
 *
 * **草稿态与生效态是两份状态**，不是一份加脏标记。一旦条件与表格数据来自同一份状态，
 * 表格重渲染时就会读到尚未生效的条件，「当前显示的是哪组条件」无法从状态里读出来，
 * 导出（复用最近一次生效的查询体）也会跟着漂移。
 *
 * 条件改动显式触发查询；例外有三类，各有理由：
 * - **首屏**：拉完作用域后按默认作用域自动查一次。这一屏是主力数据屏，用户进来就是要
 *   看数据的。延迟到点击才查的是持续增长市场屏，不是这一屏
 * - 排序 / 翻页 / 每页条数：作用于已确定的结果集，不是「条件」，走 ProTable 自身的 request
 * - 站点 / 视图改变：立即重拉日期与类目清单（小表查询），但不重查表格——否则用户会在
 *   一份属于上一个站点的日期清单里做选择
 */
/** SearchBody 里属于「筛选」的字段。行集合开关（include）不在其中——它是行集合维度。 */
const FILTER_KEYS = [
  'keyword', 'asin', 'product_title', 'brands', 'categories',
  'ranking_min', 'ranking_max', 'change_min', 'change_max',
  'click_share_min', 'click_share_max', 'conversion_share_min', 'conversion_share_max',
  'is_new',
] as const satisfies readonly (keyof SearchBody)[];

/**
 * ProTable 的复选框列宽。它不在 columns 里，但照样占横向空间——不算进 `scroll.x`
 * 的表现是最右边那一列被挤扁（列宽必须实测，见 columns.ts 的说明）。
 */
const SELECTION_COLUMN_WIDTH = 48;

export default function AnalysisPage() {
  const access = useAccess();
  const actionRef = useRef<ActionType>();

  /**
   * 勾选的结果行，用于批量加入词库。**一次性状态，不进会话快照**：切走再切回清空是
   * 预期行为，也因此这一项不需要动 CACHE_VERSION。
   */
  const [selectedKeywordIds, setSelectedKeywordIds] = useState<number[]>([]);
  const [libraryModalOpen, setLibraryModalOpen] = useState(false);

  /**
   * 挂载时读一次快照。它同时决定两件事：各 ref 的初值，以及**这次是「复原」还是
   * 「首次进入」**——后者是挂载副作用那道闸的判据。用惰性 useState 而不是直接调用，
   * 保证整个生命周期只读一次；之后所有写入都走 write-through。
   */
  const [initialSnapshot] = useState(() => readSnapshot());
  const restored = initialSnapshot !== null;

  const { initialState } = useModel('@@initialState');
  const userId = initialState?.currentUser?.id;

  /**
   * 用户保存的默认条件，挂载时同步读一次（读 localStorage 不发请求）。
   *
   * **只在没有会话快照时才读**：有快照就是「切走再切回」，那条路整段跳过初始化，保存的
   * 条件不该插进来——否则用户离开前调的条件会被他几天前保存的那组顶掉。
   *
   * 惰性 useState 保证整个生命周期只读一次。之后被清除（重置、站点失效）时用
   * `setSavedApplied(false)` 撤掉「我的默认」标记，不重读。
   */
  const [saved] = useState(() => (restored ? null : readSavedMode(userId)));
  /** 本次进屏是否确实按保存的条件初始化了——「我的默认」标记的判据。 */
  const [savedApplied, setSavedApplied] = useState(saved !== null);

  // 草稿态：用户正在编辑。初值优先用保存的条件，没有才落到内置默认（三级优先级的 2 与 3；
  // 第 1 级是快照，由 useCachedState 自己承担——有快照时下面这些初值根本不会被采用）。
  const [scope, setScope] = useCachedState(
    'scope',
    () => ({ site: saved?.site, view: saved?.view ?? 'DAY' }) as ScopeDraft,
  );
  const [filters, setFilters] = useCachedState('filters', () =>
    saved ? { ...saved.filters, q: '' } : defaultFilters(),
  );

  // 生效态：表格 request 实际读的那份。用 ref 承载，供 request 闭包读取。
  const appliedRef = useRef<SearchBody | null>(initialSnapshot?.applied ?? null);
  // 生效态的视图单独提出来做 state：列集合（走势/周变化）随它变，需要触发重渲染
  const [appliedView, setAppliedView] = useCachedState('appliedView', 'DAY' as View);
  // 生效条件的签名，与草稿签名比较得出 dirty，不额外维护脏标记
  const [appliedSig, setAppliedSig] = useCachedState('appliedSig', '');

  const [sites, setSites] = useCachedState('sites', [] as SiteOption[]);
  const [availableDates, setAvailableDates] = useCachedState('availableDates', [] as string[]);
  const [categories, setCategories] = useCachedState('categories', [] as CategoryOption[]);
  // 导出中的状态**不进缓存**：它描述的是一次正在进行的请求，而请求随组件一起没了。
  // 缓存它会让用户切回来看到一个永远转下去的按钮。
  const [exporting, setExporting] = useState(false);
  /**
   * 作用域元数据是否已经取回。**不进缓存**：复原路径不调 `reloadScopeMeta`，但快照里
   * 的 `availableDates` 本就是取回过的，故初值直接跟着 `restored` 走。
   */
  const [scopeLoaded, setScopeLoaded] = useState(restored);
  const [filterOpen, setFilterOpen] = useCachedState('filterOpen', false);
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

  /**
   * 滚动位置的恢复。
   *
   * **必须等结果行落到 DOM 之后**：行还没渲染时页面高度不足，`scrollTo` 会被截断到
   * 当前最大滚动量，停在一个与离开前对不上的位置——而这种错误没有任何地方会报错。
   * 故由 `request` 交付数据后调 `markDataReady`，再在 effect 里滚。
   *
   * **只恢复一次**：不清标记的话，用户在本屏内自己滚动、触发任一次重渲染都会被弹回。
   */
  /** 复原的缓存行是否已经交付过。一次性——之后所有请求都真的去查（见 request 里的注释）。 */
  const restoreServedRef = useRef(false);
  const pendingScrollRef = useRef<number | null>(restored ? (initialSnapshot?.scrollY ?? 0) : null);
  const [dataTick, setDataTick] = useState(0);
  const markDataReady = () => setDataTick((n) => n + 1);

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
        writeSlot({ scrollY: window.scrollY });
        queued = false;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  /**
   * 是否已经查过一次。`appliedSig` 只在 `apply()` 里被写非空值，所以它就是这个判据，
   * 不用再养一个独立标记。
   *
   * 首屏自动查询之后，它只剩两个用途：**结果条数的显隐**与「有未生效改动」提示的前置。
   * 已经不再是空表格文案的判据——那件事交给 `scopeHasNoDates`（见下）。
   *
   * 条数仍然跟着它而不是跟着 `scopeHasNoDates`：用户切到一个没有数据的站点时表格**不**
   * 重查（这是既有约定），屏上仍是上一个站点的行，那时条数必须继续显示，因为它描述的
   * 正是那批还在屏上的行。
   */
  const hasQueried = appliedSig !== '';

  /**
   * 当前作用域一期数据都没有。这是首屏自动查询之后「空表格」唯一还剩的非零行成因——
   * 没有日期可查，`request` 开头那道 `!body?.date` 守卫会直接返回空、连请求都不发。
   *
   * 必须与「查出来是 0 行」分开：显示「搜索结果：0」会被读成条件把行筛没了，用户会去
   * 排查筛选条件，而真正的原因是这个站点根本没导过数据。
   *
   * `scopeLoaded` 这道前置是为了压掉挂载那一瞬——那时 `availableDates` 还是初值空数组，
   * 不加前置会先闪一下「还没有导入任何数据」再出数据，而那句话在当时是错的。
   */
  const scopeHasNoDates = scopeLoaded && availableDates.length === 0;

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
   */
  const apply = (body: SearchBody, sig: string) => {
    appliedRef.current = body;
    needsTotalRef.current = true;
    writeSlot({ applied: body, needsTotal: true });
    setAppliedSig(sig);
    setAppliedView(body.view);
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
    const scopeOnly = toSearchBody(scope, { ...defaultFilters(), q: filters.q });
    const body: SearchBody = {
      ...(appliedRef.current ?? scopeOnly),
      site: scopeOnly.site,
      view: scopeOnly.view,
      date: scopeOnly.date,
      keyword: scopeOnly.keyword,
      asin: scopeOnly.asin,
    };
    apply(body, JSON.stringify(body));
  };

  const submitFilters = () => {
    const current = appliedRef.current ?? draftBody;
    const body: SearchBody = {
      ...draftBody,
      site: current.site,
      view: current.view,
      date: current.date,
      keyword: current.keyword,
      asin: current.asin,
    };
    apply(body, JSON.stringify(body));
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
    setFilters((current) => ({ ...defaultFilters(), q: current.q }));
    clearSavedMode(userId);
    setSavedApplied(false);
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
    // q 不入库：检索词是一次性查找，不是「常看的模式」。解构剥掉它，`void` 是为了让
    // lint 看到这个变量确实被「用」了（本项目没开 ignoreRestSiblings）
    const { q, ...savableFilters } = filters;
    void q;
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
   * 查不出任何数据且原因不可见的条件。
   *
   * **草稿与生效两份都要清。** 类目清单是按 (站点, 视图) 取的，作用域一变两边的
   * 类目就同时作废。只清草稿的话，`submitScope` 会把生效态里那份旧作用域的类目
   * 原样带进下一次查询——界面上一个勾都没有，表格却查不出数据，正是这里要避免的
   * 那种不可见条件。
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
      // brands 一并清：品牌筛选面板暂时下线，但字段仍在模型里，留着不清会成为
      // 一个界面上看不见却在生效的条件
      setFilters((f) => ({ ...f, categories: [], brands: [] }));
      if (appliedRef.current) {
        appliedRef.current = { ...appliedRef.current, categories: undefined, brands: undefined };
        writeSlot({ applied: appliedRef.current });
      }
    }
    reloadCategories(s.site, nextView, s.latest_date);
    return s;
  };

  /**
   * 解析首屏用哪个作用域与哪份筛选。
   *
   * 没有保存项时就是老路：默认站点 + 按天 + 内置默认筛选。
   *
   * 有保存项时**乐观**拿保存的站点去问 `/scope`：站点若已从启用清单移除，后端报错
   * （「非法站点报错不回退」是既有约定，`DEFAULT_SITE` 只用于未传站点的情况）。这里
   * 捕获后回退到默认站点、丢掉失效的保存项、并**自行提示**——那次调用带了
   * `skipErrorHandler`，全局错误提示不会出声，不自己说就成了静默降级：用户以为在看
   * 自己保存的站点，实际在看另一个。
   *
   * 之所以不先 `getSites()` 校验再发：那把首屏两个并行请求变成串行，多一个往返，
   * 而站点下线是罕见事件——不值得让每次进屏都变慢。
   */
  const resolveInitialScope = async () => {
    if (!saved) {
      const s = await reloadScopeMeta(undefined, 'DAY');
      return { scope: s, filters: defaultFilters(), view: 'DAY' as View };
    }
    try {
      const s = await reloadScopeMeta(saved.site, saved.view, {
        keepCategories: true,
        skipErrorHandler: true,
      });
      return { scope: s, filters: { ...saved.filters, q: '' }, view: saved.view };
    } catch {
      clearSavedMode(userId);
      setSavedApplied(false);
      // 草稿也要跟着回到内置默认：保存项已作废，界面上却还显示着它的筛选与排序，
      // 用户会以为那组条件仍在生效
      setFilters(defaultFilters());
      setSortDraft({ field: 'current_ranking', dir: 'asc' });
      message.warning('你保存的站点已下线，已切回默认站点。保存的条件已清除。');
      const s = await reloadScopeMeta(undefined, 'DAY');
      return { scope: s, filters: defaultFilters(), view: 'DAY' as View };
    }
  };

  useEffect(() => {
    // 从缓存复原：整段初始化跳过，一个请求都不发。表格那次 request 会命中短路分支，
    // 拿缓存行渲染。**只跳过挂载初始化**——用户动作触发的请求（改站点/视图重拉
    // scope 与 categories、点「立即查询」）一律照旧。
    //
    // 这道闸必须留在最前面：自动查询写在它之后，才能继承「有缓存就整段跳过」的语义。
    // 顺序反过来会让每次切回都多查一次。
    if (restored) return;
    getSites()
      .then(setSites)
      .catch(() => undefined);
    // 首屏拉完作用域元数据后**立即按默认作用域查一次**——这一屏是主力数据屏，用户
    // 进来就是要看数据的，让他先点一下才出数据是多余的一步。默认作用域是默认站点 +
    // 按天 + 最新一期 + **默认筛选（只看新词）**，回答的是「今天有哪些新词」。
    //
    // 筛选必须用 `defaultFilters()`，与初始草稿同源——若这里改用别的值，表格显示的
    // 就不是筛选控件所声称的那组条件。
    //
    // 带筛选有两个后果，都是既有语义、不是本屏特例：总数走 `count_filtered` 精确
    // 实算而非 `site_data_stats` 的 O(1) 预存值（日报实测约 168ms）；**掉榜行不在
    // 结果内**——它们在锚定期没有行可与筛选匹配。
    //
    // **须由 `reloadScopeMeta` 返回的 `s` 组装条件，不能读 `scope` 状态**：它刚被
    // `setScope` 写过，而 setState 是异步的，此刻读到的还是初值（date 为空），
    // 组装出来的条件会被 request 开头那道 `!body?.date` 守卫挡掉——表现是首屏静悄悄
    // 地什么都不查，和改动前一模一样，且没有任何报错。
    //
    // 用户保存过条件时，作用域改用保存的站点与视图，筛选与排序取保存的那份；日期仍是
    // 该作用域的最新一期（保存项从不存日期）。
    resolveInitialScope()
      .then((resolved) => {
        if (!resolved) return;
        const { scope: s, filters: initialFilters, view } = resolved;
        if (!s.latest_date) return; // 该站点一期数据都没有，不发请求
        const body = toSearchBody({ site: s.site, view, date: s.latest_date }, initialFilters);
        apply(body, JSON.stringify(body));
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // 站点域名按**生效作用域**解析：切了站点没点查询时，表格数据仍属上一个站点，
  // 链接必须跟着数据走，否则用户点进去搜的是另一个站点。
  const appliedDomain = useMemo(
    () => sites.find((s) => s.code === appliedRef.current?.site)?.domain,
    [sites, appliedSig],
  );
  const columns = useMemo(
    () => buildColumns(appliedView, appliedDomain),
    [appliedView, appliedDomain],
  );

  return (
    <PageContainer title="ABA数据选品">
      <Card size="small" styles={{ body: { padding: 16 } }}>
        <ScopeBar
          sites={sites}
          availableDates={availableDates}
          scope={scope}
          q={filters.q}
          dirty={dirty}
          savedApplied={savedApplied}
          onScopeChange={onScopeChange}
          onQChange={(q) => setFilters((f) => ({ ...f, q }))}
          onSubmit={submitScope}
        />
      </Card>

      <Card
        size="small"
        style={{ marginTop: 12 }}
        // 左右内边距取 24，与下方表格对齐：表格那块是 ProCard（无边框、body 内边距 24），
        // 内容线落在 40 + 24 = 64；这张是 Card size="small"（1px 边框 + head 内边距 12），
        // 默认落在 53，两行差 11px，肉眼看得出来。只调 paddingInline，不动上下内边距——
        // 那会改变行高。
        //
        // 有 1px 边框在，24 实际落到 65 而非 64。差这 1px 是边框吃掉的，要抹平只能让这张
        // 卡片也无边框，那是另一种外观，没顺手做。
        styles={{ header: { paddingInline: 24 }, body: { paddingInline: 24 } }}
        title={
          <Row gutter={16} wrap={false} style={{ width: '100%' }}>
            <Col flex="260px">
              <Typography.Text strong>选择筛选类目</Typography.Text>
            </Col>
            <Col flex="auto" style={{ paddingLeft: 16 }}>
              {/*
                标题「搜索结果过滤」的**右边缘**与下方条件标签的右边缘对齐（「滤」对「合」）：
                标签定宽 LABEL_WIDTH 且右对齐，标题若从列左缘起排就飘在条件外面。

                用 flex + justify-content: flex-end，**不是** inline-block + text-align: right：
                后者在标题宽于盒子时会从左缘起排并向右溢出，右对齐等于失效、右边缘反而越过
                对齐线；flex 在溢出时把内容推出起始侧，右边缘无论多宽都钉在 LABEL_WIDTH 上。

                宽度取自 FilterPanel 导出的同一个常量——两处各写一个数值迟早分叉，
                而分叉的表现就是标题又飘开。
              */}
              <span
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  width: LABEL_WIDTH,
                  whiteSpace: 'nowrap',
                }}
              >
                <Typography.Text strong>搜索结果过滤</Typography.Text>
              </span>
            </Col>
          </Row>
        }
        extra={
          // 边框与文字走主色（outlined 变体），不是默认的灰边：这个按钮是过滤区唯一的
          // 入口，灰边会让它退到与卡片边框同一层次，看起来像装饰而不是可点的东西。
          <Button
            color="primary"
            variant="outlined"
            size="small"
            icon={filterOpen ? <UpOutlined /> : <DownOutlined />}
            onClick={() => setFilterOpen((v) => !v)}
          >
            {filterOpen ? '隐藏过滤条件' : '展开过滤条件'}
          </Button>
        }
      >
        {filterOpen && (
          <>
            <Row gutter={16}>
              {/*
                minWidth: 0 不是可有可无的——flex 子项默认 min-width: auto，内容的最小宽度
                会顶破 flex-basis。类目名不截断时（见 OptionPanel）这一列被撑到约 302px，
                把右边整列推出 42px，表现就是标题与条件标签对不齐。
              */}
              <Col flex="260px" style={{ minWidth: 0 }}>
                <CategoryPanel
                  options={categories}
                  value={filters.categories}
                  onChange={(categories) => setFilters((f) => ({ ...f, categories }))}
                />
              </Col>
              <Col flex="auto" style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 16 }}>
                <FilterPanel value={filters} onChange={setFilters} />
              </Col>
            </Row>
            {/* 负 margin 让这条分隔线铺满卡片宽度，左右两个值必须等于 body 的 paddingInline
                （见上面 styles），改了那边这里要跟着改，否则线到不了卡片边缘。 */}
            {/* 负 margin 让这条分隔线铺满卡片宽度，左右两个值必须等于 body 的 paddingInline
                （见上面 styles），改了那边这里要跟着改，否则线到不了卡片边缘。

                **上下留白要对称**，而下方的留白由**两层**共同构成：这里的 `padding-bottom`
                加上 Card body 自己的 12px。原来错在底部那个 `-12px` 负 margin——它把卡片
                的下内边距整个抵消掉，按钮下方只剩 4px，而上方有 16px，肉眼就是歪的。
                去掉负 margin 后 4 + 12 + 1(边框) = 17px，与上方的 17px 相等（实测值，
                不是推算：这一屏的像素问题只信 getBoundingClientRect）。 */}
            <div
              style={{
                margin: '20px -24px 0',
                padding: '16px 0 4px',
                borderTop: '1px solid #f0f0f0',
                textAlign: 'center',
              }}
            >
              <Button type="primary" icon={<FilterOutlined />} onClick={submitFilters}>
                开始筛选
              </Button>
              <Button icon={<ReloadOutlined />} style={{ marginLeft: 12 }} onClick={resetFilters}>
                重置条件
              </Button>
              {/* 作用域没有可查日期、一次都没查成时禁用：那时没有「当下生效的条件」可存，
                  存下去是一份空条件 */}
              <Tooltip
                title={
                  appliedRef.current?.site
                    ? '下次登录进来默认就是这组条件（不含日期与检索词）'
                    : '还没有查询结果，先查一次再保存'
                }
              >
                <Button
                  icon={<SaveOutlined />}
                  style={{ marginLeft: 12 }}
                  disabled={!appliedRef.current?.site}
                  onClick={saveCurrentMode}
                >
                  保存当前模式
                </Button>
              </Tooltip>
            </div>
          </>
        )}
      </Card>

      {pendingRebuild && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="该日期的品牌与类目尚未重建"
          description="这份数据导入时品牌与类目还存放在商品维度上，需要从原始文件重导才能按名次筛选。在此之前，按品牌或类目筛选这个日期会得到空结果——那是数据待重建，不是你的筛选条件写错了。"
        />
      )}

      <ProTable<AnalysisRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="keyword_id"
        columns={columns}
        // **`tableLayout` 必须显式给。** rc-table 只在四种情形下才用 fixed：显式传入、
        // 有固定列、有 scroll.y 或粘性头、或某列开了 ellipsis。**给 `scroll.x` 不在其中**
        // ——早期版本算，升级后不算了，而这种退化不报错：列上的 width 悄悄变回建议值，
        // 浏览器按内容抢宽度（实测关键词列 220 渲染成 247，表格 1578 撑到 1792）。见 #43。
        tableLayout="fixed"
        // 总宽给 scroll.x：窗口比它窄时横向滚动，而不是把列压扁。
        scroll={{ x: totalColumnWidth(appliedView) + SELECTION_COLUMN_WIDTH }}
        // 勾选若干行后可批量「加入词库」。**选中状态不进会话快照**：它是一次性的界面
        // 状态，切走再切回清空是预期行为，因此这一项不需要动 CACHE_VERSION。
        rowSelection={{
          // 选择列必须显式给宽：它不给宽度时 colgroup 里是空值，而 `scroll.x` 那边
          // 已经按 48 把它算进去了，两处对不上就是横向多出/少掉一截。
          columnWidth: SELECTION_COLUMN_WIDTH,
          selectedRowKeys: selectedKeywordIds,
          onChange: (keys) => setSelectedKeywordIds(keys as number[]),
        }}
        // 筛选全部在上面的过滤区，表格不再自带条件表单
        search={false}
        // 空表格有两种含义，必须说清是哪一种（见 scopeHasNoDates 的注释）
        locale={
          scopeHasNoDates
            ? { emptyText: '该站点在当前视图下还没有导入任何数据' }
            : undefined
        }
        // 页码与每页条数由 ProTable 自己管，这里只给初值——复原时给回离开前那一页，
        // 否则表格会从第 1 页起请求，短路判定（含页码）随即失配，白跑一次查询。
        // 用 default* 而不是受控的 pageSize：后者会和 showSizeChanger 抢同一个值。
        pagination={{
          defaultCurrent: initialSnapshot?.lastRequest?.page ?? 1,
          defaultPageSize: initialSnapshot?.lastRequest?.perPage ?? 50,
          showSizeChanger: true,
        }}
        dateFormatter="string"
        options={false}
        headerTitle={
          <Space size="middle">
            {access.canExport && (
              <Button
                icon={<DownloadOutlined />}
                loading={exporting}
                disabled={!appliedRef.current?.date}
                onClick={onExport}
              >
                导出 CSV
              </Button>
            )}
            {/*
              加入词库只在选中若干行后出现——没选行时它是个点了没有意义的按钮。
              掉榜行同样可选：它有 keyword_id，而「掉出去了想继续盯」正是加入词库
              最典型的动机。
            */}
            {access.canViewToolbox && selectedKeywordIds.length > 0 && (
              <Button
                // 实心：它只在勾选之后才出现，是那一刻用户唯一要做的动作，
                // 与常驻的「导出 CSV」不同级。
                type="primary"
                icon={<StarOutlined />}
                onClick={() => setLibraryModalOpen(true)}
              >
                加入词库（{selectedKeywordIds.length}）
              </Button>
            )}
            {/*
              没查过就不显示条数。显示「搜索结果：0」会被读成「这个条件查出来是 0 行」，
              而实际是「还没查」——两者在界面上必须能分开。
            */}
            {hasQueried && (
              <Typography.Text type="secondary">
                搜索结果：<Typography.Text strong>{resultTotal.toLocaleString()}</Typography.Text>
              </Typography.Text>
            )}
            {/*
              筛选一生效，掉榜行必然全部消失——筛选以锚定日那一行为准，而掉榜行在那一天
              没有行可供匹配。不说出来的话，用户看到的是「加了个筛选，掉榜的词全没了」，
              会当成数据缺失去排查。读的是**生效条件**而不是草稿：草稿改了还没查时，
              表格里仍是上一组条件的结果，提示必须跟着数据走。
            */}
            {appliedHidesDropped && (
              <Tooltip title="筛选以所选日期那一行为准，而掉榜行在该日期没有数据，无法参与筛选">
                <Typography.Text type="warning">本次结果不含掉榜行</Typography.Text>
              </Tooltip>
            )}
          </Space>
        }
        request={async (params) => {
          const body = appliedRef.current;
          if (!body?.date) return { data: [], total: 0, success: true };
          const key: RequestKey = {
            body,
            orderBy: appliedSortRef.current.field,
            orderDir: appliedSortRef.current.dir,
            page: params.current ?? 1,
            perPage: params.pageSize ?? 50,
          };
          // 短路只作用于**复原后的第一次请求**（切回来那一次），一次性。
          //
          // 不能改成「参数相同就用缓存」：那样同参数的「立即查询」也会被短路，
          // 而它是用户强制刷新的唯一入口——数据刚导完想看新的，点了却什么都不动。
          // 翻页、改排序、重新查询一律真的去查。
          //
          // 仍然要 `sameRequest` 核对：万一表格请求的页码/排序与缓存那次对不上
          // （如页码没复原成功），拿缓存行去填就是显示了另一页的数据。
          if (restored && !restoreServedRef.current) {
            restoreServedRef.current = true;
            const cached = readSnapshot();
            if (cached && sameRequest(key, cached.lastRequest)) {
              markDataReady();
              return { data: cached.rows, total: cached.total, success: true };
            }
          }
          // 排序只有工具栏一个入口（列上没有 sorter），故不读 ProTable 的 sort 参数——
          // 读了也永远是空对象，那种分支看着像在工作，实际是死代码。
          // 只有条件变更那次要重算总数；翻页/改排序沿用缓存值（见 apply）。
          // 标记在**拿到成功响应之后**才清——请求失败时保持置位，重试仍会带上重算，
          // 否则一次失败会让这组条件永远拿不到总数。
          const wantTotal = needsTotalRef.current;
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
          totalRef.current = total;
          setResultTotal(total);
          setPendingRebuild(res.pending_rebuild === true);
          const rows = res.data ?? [];
          // 把「这次请求是什么 + 拿回了什么」一并落进快照，供下次挂载短路
          writeSlot({ lastRequest: key, rows, total, needsTotal: needsTotalRef.current });
          markDataReady();
          return { data: rows, total, success: res.success !== false };
        }}
        toolBarRender={() => [
          <Select
            key="sort-field"
            style={{ width: 140 }}
            value={sortDraft.field}
            onChange={(field) => setSortDraft((current) => ({ ...current, field }))}
            options={[
              { label: '当前排名', value: 'current_ranking' },
              { label: '排名变化', value: 'ranking_change' },
              { label: '历史排名', value: 'previous_ranking' },
              { label: '点击份额', value: 'top1_click_share' },
              { label: '转化份额', value: 'top1_conversion_share' },
              { label: '新词', value: 'is_new' },
            ]}
          />,
          <Select
            key="sort-direction"
            style={{ width: 100 }}
            value={sortDraft.dir}
            onChange={(dir) => setSortDraft((current) => ({ ...current, dir }))}
            options={[
              { label: '升序', value: 'asc' },
              { label: '降序', value: 'desc' },
            ]}
          />,
          <Button
            key="apply-sort"
            type="primary"
            onClick={() => {
              appliedSortRef.current = sortDraft;
              writeSlot({ appliedSort: sortDraft });
              actionRef.current?.reload(true);
            }}
          >
            确定
          </Button>,
        ]}
      />

      <AddToLibraryModal
        open={libraryModalOpen}
        // 站点取**生效作用域**而非草稿：草稿切了站点但没查时，表格里仍是上一个站点的行，
        // 可选分组必须跟着数据走，否则会把 US 的词加进 DE 的分组（服务端会拒，但那是
        // 一次本可避免的失败）。
        site={appliedRef.current?.site}
        keywordIds={selectedKeywordIds}
        onClose={() => setLibraryModalOpen(false)}
        onAdded={() => setSelectedKeywordIds([])}
      />
    </PageContainer>
  );
}
