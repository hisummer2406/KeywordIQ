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
import { useAccess, useModel } from '@umijs/max';
import { Alert, Button, Card, Col, Row, Select, Space, Tooltip, Typography, message } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { type AnalysisRow, type View } from '@/services/analysis';
import KeywordTrendDrawer from '@/components/KeywordTrendDrawer';
import AddToLibraryModal from './AddToLibraryModal';
import CategoryPanel from './CategoryPanel';
import FilterPanel, { LABEL_WIDTH } from './FilterPanel';
import FirstListedSummary from './FirstListedSummary';
import ScopeBar from './ScopeBar';
import './AnalysisTable.less';
import { buildColumns, totalColumnWidth } from './columns';
import { defaultFilters, toSearchBody } from './filters';
import { clearSavedMode, readSavedMode } from './savedMode';
import { useCachedState } from './useCachedState';
import { useAnalysisQuery } from './useAnalysisQuery';
import { useAnalysisScope } from './useAnalysisScope';
import { useAnalysisSession } from './useAnalysisSession';
import { useVisitedKeywords } from './visitedKeywords';

/**
 * 分析查询屏：作用域 / 过滤 / 结果三段。
 *
 * 状态编排拆成三个 hook，本文件只留**跨模块协调**与 JSX 组合：
 *
 * - `useAnalysisSession` —— 会话快照、复原短路、滚动位置
 * - `useAnalysisScope`   —— 站点/视图/日期与它们带出的选项清单
 * - `useAnalysisQuery`   —— 草稿态与生效态、排序、总数、导出、表格请求
 *
 * 三者之间没有直接 import，依赖方向单向（session ← scope / query ← 本文件）。
 * 见 openspec 变更 `split-analysis-page-state` 的 design.md。
 *
 * 条件改动显式触发查询；例外有三类，各有理由：
 * - **首屏**：拉完作用域后按默认作用域自动查一次。这一屏是主力数据屏，用户进来就是要
 *   看数据的。延迟到点击才查的是持续增长市场屏，不是这一屏
 * - 排序 / 翻页 / 每页条数：作用于已确定的结果集，不是「条件」，走 ProTable 自身的 request
 * - 站点 / 视图改变：立即重拉日期与类目清单（小表查询），但不重查表格——否则用户会在
 *   一份属于上一个站点的日期清单里做选择
 */

/**
 * ProTable 的复选框列宽。它不在 columns 里，但照样占横向空间——不算进 `scroll.x`
 * 的表现是最右边那一列被挤扁（列宽必须实测，见 columns.ts 的说明）。
 */
const SELECTION_COLUMN_WIDTH = 48;

/**
 * 固定表头 / 分页条的偏移（与 `AnalysisTable.less` 保持一致）：顶栏 56px，其下 48px 的
 * 分页条，表头贴在分页条下面。
 */
const STICKY_NAV_HEIGHT = 56;
const STICKY_PAGER_HEIGHT = 48;

/**
 * 翻页后把表格第一行带回可见区域。只在表头已被滚出自然位置（用户在长表里往下看了）时才滚，
 * 且只动纵向——横向滚动位置留给表格自己的滚动容器，翻页前后在看同一列。
 */
function scrollTableToTop() {
  const table = document.querySelector('.analysis-table .ant-table');
  if (!table) return;
  const top = table.getBoundingClientRect().top;
  const target = STICKY_NAV_HEIGHT + STICKY_PAGER_HEIGHT;
  if (top >= target) return;
  window.scrollTo({ top: window.scrollY + top - target });
}

export default function AnalysisPage() {
  const access = useAccess();

  /**
   * 勾选的结果行，用于批量加入词库。**一次性状态，不进会话快照**：切走再切回清空是
   * 预期行为，也因此这一项不需要动 CACHE_VERSION。
   */
  const [selectedKeywordIds, setSelectedKeywordIds] = useState<number[]>([]);
  const [libraryModalOpen, setLibraryModalOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useCachedState('filterOpen', false);
  // 用户翻页 / 改每页条数后，等新一页的行落进 DOM 再回顶。不在 onChange 里直接滚：
  // 那一刻旧页的行还在，页面高度没变，滚过去之后新数据一到又会跳。会话复原的首次加载
  // 不经过 onChange，因此不会触发——复原的滚动位置由 useAnalysisSession 负责。
  const scrollAfterLoadRef = useRef(false);

  const session = useAnalysisSession();
  const { initialSnapshot, restored } = session;

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
   *
   * **留在本文件而不进任何一个 hook**：它同时喂作用域的初值（站点/视图）与查询的初值
   * （筛选/排序），谁都不该独占。
   */
  const [saved] = useState(() => (restored ? null : readSavedMode(userId)));

  /**
   * 两个 hook 的接线是一个环：作用域失效时要清掉筛选（scope → query），而组装查询体
   * 又要读作用域草稿（query → scope）。调用顺序上只能有一个在先，故把回调经 ref 间接
   * 化——scope 先建起来，拿到的是一个此刻还指向空操作、渲染结束前就被填上的引用。
   *
   * 这不是可以省掉的绕路：真正的替代方案是把筛选并回作用域里，而那正是拆分要消除的
   * 东西（类目在两边同名却生命周期相反，见 useAnalysisScope 的模块注释）。
   */
  const clearCategoryFiltersRef = useRef<() => void>(() => {});

  const scopeState = useAnalysisScope({
    saved,
    restored,
    onScopeInvalidated: () => clearCategoryFiltersRef.current(),
  });
  const { scope, sites, availableDates, categories, scopeHasNoDates } = scopeState;

  const query = useAnalysisQuery({
    scope,
    session,
    saved,
    userId,
  });
  clearCategoryFiltersRef.current = query.clearCategoryFilters;

  const { appliedRef, actionRef, appliedView, loadedSite, loadedView, loadedDate } = query;

  /**
   * 排名趋势详情：一次只开一行的详情，不进会话快照——与选中行同理，切走再切回清空是
   * 预期行为。
   *
   * **点击那一刻把行与它所属的作用域一起冻结进 `trendTarget`，不是分开存。**
   * `loadedSite`/`loadedView`/`loadedDate` 是随最近一次响应更新的活状态：若打开详情后、
   * 抽屉关闭前又有一轮查询完成（哪怕是这次点击之前就在飞的旧请求），三者会先于用户手动
   * 关闭抽屉而变化，届时抽屉会把仍在展示的旧行 `keyword_id` 配上新站点/视图/日期重新
   * 发请求——跨站点必 422，同站点也会把数据按错误的日期语境解析（PR #87 复审 P2，
   * 修复第一版把冻结做在了渲染处读取 `loadedSite` 等活状态，仍然会被后续响应覆盖）。
   *
   * `openTrend` 必须用 `useCallback` 而不是每次渲染都新建一个闭包：它被烤进下面
   * `columns` 的 `useMemo`，而那个 memo 的依赖列表里没有（也不该有）`loadedSite`
   * 这三个高频变化的活状态——`columns` 要的是"作用域变了才重建列"，不是"结果一到就
   * 重建列"。会话复原时首次渲染 `loadedSite` 等仍是初始的 `undefined`，`runRequest`
   * 随后才异步把它们填上；若 `openTrend` 不是 `useCallback` 记忆的稳定引用，`columns`
   * 的 memo 不会因为这几个状态变化而重算，烤进去的还是复原首次渲染那个永远读到
   * `undefined` 的旧闭包——点击行时守卫静默 return，抽屉打不开，直到某个真正触发
   * `columns` 重算的依赖（如切视图）恰好也发生（PR #87 复审第三轮 P2）。
   */
  interface TrendTarget {
    row: AnalysisRow;
    site: string;
    view: View;
    date?: string;
  }
  const [trendTarget, setTrendTarget] = useState<TrendTarget | null>(null);
  const openTrend = useCallback(
    (row: AnalysisRow) => {
      if (!loadedSite || !loadedView) return; // 一期数据都没有时表格本就是空的，点不到行
      setTrendTarget({ row, site: loadedSite, view: loadedView, date: loadedDate ?? undefined });
    },
    [loadedSite, loadedView, loadedDate],
  );

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
   *
   * **这段横跨三个 hook，故留在本文件**：放进 scope 就要 import query 的 setter，
   * 放进 query 就要 import scope 的 `reloadScopeMeta`，两种放法都会给单向的依赖图
   * 添一条回边。协调三个模块的代码放在协调者这里不需要额外理由。
   */
  const resolveInitialScope = async () => {
    if (!saved) {
      const s = await scopeState.reloadScopeMeta(undefined, 'DAY');
      return { scope: s, filters: defaultFilters(), view: 'DAY' as View };
    }
    try {
      const s = await scopeState.reloadScopeMeta(saved.site, saved.view, {
        keepCategories: true,
        skipErrorHandler: true,
      });
      return {
        scope: s,
        filters: { ...saved.filters, q: '', qMode: 'ALL_TERMS' as const },
        view: saved.view,
      };
    } catch {
      clearSavedMode(userId);
      query.setSavedApplied(false);
      // 草稿也要跟着回到内置默认：保存项已作废，界面上却还显示着它的筛选与排序，
      // 用户会以为那组条件仍在生效
      query.setFilters(defaultFilters());
      query.setSortDraft({ field: 'current_ranking', dir: 'asc' });
      message.warning('你保存的站点已下线，已切回默认站点。保存的条件已清除。');
      const s = await scopeState.reloadScopeMeta(undefined, 'DAY');
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
    scopeState.loadSites();
    // 首屏拉完作用域元数据后**立即按默认作用域查一次**——这一屏是主力数据屏，用户
    // 进来就是要看数据的，让他先点一下才出数据是多余的一步。默认作用域是默认站点 +
    // 按天 + 最新一期 + **默认筛选（只看首次入榜）**，回答的是「本期首次入榜了哪些词」。
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
        query.apply(body);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 站点域名按**生效作用域**解析：切了站点没点查询时，表格数据仍属上一个站点，
  // 链接必须跟着数据走，否则用户点进去搜的是另一个站点。
  const appliedDomain = useMemo(
    () => sites.find((s) => s.code === appliedRef.current?.site)?.domain,
    [sites, appliedRef, query.appliedSig],
  );
  /**
   * 悬浮历史（issue #96）的作用域三元组必须来自 `loadedSite`/`loadedView`/`loadedDate`
   * ——已完成响应的活状态，不是可能仍在飞的 `appliedRef`。理由与 `openTrend` 用同一组
   * 状态相同（见上方 `TrendTarget` 注释）：表格当前渲染的行本身就是上一次完成响应的
   * 数据，悬浮历史查的是"这一行现在显示的这个 ASIN/品牌"，必须与行数据同一个作用域。
   * 三者任一缺失（还没查出首屏数据）时不提供悬浮入口。
   */
  const historyScope = useMemo(
    () => (loadedSite && loadedView && loadedDate ? { site: loadedSite, view: loadedView, on: loadedDate } : undefined),
    [loadedSite, loadedView, loadedDate],
  );
  // 关键词已访问标记（issue #125）：本浏览器长期保留，按用户、站点隔离。
  const visitedStore = useVisitedKeywords(userId);
  // 访问记录的站点必须是**表格里这批行实际所属的站点**（`loadedSite`），不是 `appliedRef`：
  // 换站点查询时 `apply()` 同步改了 appliedRef，而旧站点的行在响应落地前仍在屏上，
  // 此时点击会把旧站点的词记到新站点名下，且这份历史是持久的、事后无从分辨。
  // 列定义传入的 siteCode 因此被忽略，一律用 loadedSite。
  const visited = useMemo(
    () => ({
      isVisited: (_site: string | undefined, keyword: string) =>
        visitedStore.isVisited(loadedSite, keyword),
      markVisited: (_site: string | undefined, keyword: string) =>
        visitedStore.markVisited(loadedSite, keyword),
    }),
    [visitedStore, loadedSite],
  );
  const columns = useMemo(
    () =>
      buildColumns(
        appliedView,
        appliedDomain,
        openTrend,
        appliedRef.current?.site,
        historyScope,
        visited,
      ),
    [appliedView, appliedDomain, appliedRef, query.appliedSig, openTrend, historyScope, visited],
  );

  return (
    <PageContainer title="关键词市场分析">
      <Card size="small" styles={{ body: { padding: 16 } }}>
        <ScopeBar
          sites={sites}
          availableDates={availableDates}
          scope={scope}
          q={query.filters.q}
          qMode={query.filters.qMode}
          dirty={query.dirty}
          savedApplied={query.savedApplied}
          onScopeChange={scopeState.onScopeChange}
          onQChange={(q) => query.setFilters((f) => ({ ...f, q }))}
          onQModeChange={(qMode) => query.setFilters((f) => ({ ...f, qMode }))}
          onSubmit={query.submitScope}
        />
        <div style={{ marginTop: 8 }}>
          <FirstListedSummary site={loadedSite} view={loadedView} date={loadedDate} />
        </div>
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
                  value={query.filters.categories}
                  onChange={(categories) => query.setFilters((f) => ({ ...f, categories }))}
                />
              </Col>
              <Col flex="auto" style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 16 }}>
                <FilterPanel
                  value={query.filters}
                  onChange={query.setFilters}
                />
              </Col>
            </Row>
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
              <Button type="primary" icon={<FilterOutlined />} onClick={query.submitFilters}>
                开始筛选
              </Button>
              <Button
                icon={<ReloadOutlined />}
                style={{ marginLeft: 12 }}
                onClick={query.resetFilters}
              >
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
                  onClick={query.saveCurrentMode}
                >
                  保存当前模式
                </Button>
              </Tooltip>
            </div>
          </>
        )}
      </Card>

      {query.pendingRebuild && (
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
        className="analysis-table"
        // 表头粘在顶栏与分页条之下；横向滚动由 rc-table 同步表头与表体。
        sticky={{ offsetHeader: STICKY_NAV_HEIGHT + STICKY_PAGER_HEIGHT }}
        onLoad={() => {
          if (!scrollAfterLoadRef.current) return;
          scrollAfterLoadRef.current = false;
          requestAnimationFrame(scrollTableToTop);
        }}
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
          scopeHasNoDates ? { emptyText: '该站点在当前视图下还没有导入任何数据' } : undefined
        }
        // 页码与每页条数由 ProTable 自己管，这里只给初值——复原时给回离开前那一页，
        // 否则表格会从第 1 页起请求，短路判定（含页码）随即失配，白跑一次查询。
        // 用 default* 而不是受控的 pageSize：后者会和 showSizeChanger 抢同一个值。
        pagination={{
          defaultCurrent: initialSnapshot?.lastRequest?.page ?? 1,
          defaultPageSize: initialSnapshot?.lastRequest?.perPage ?? 50,
          showSizeChanger: true,
          // 只保留这一套分页入口，放在表头上方右侧并随表头一起固定（见 AnalysisTable.less）
          position: ['topRight'],
          className: 'analysis-pager',
          onChange: () => {
            scrollAfterLoadRef.current = true;
          },
        }}
        dateFormatter="string"
        options={false}
        headerTitle={
          <Space size="middle">
            {access.canExport && (
              <Button
                icon={<DownloadOutlined />}
                loading={query.exporting}
                disabled={!appliedRef.current?.date}
                onClick={query.onExport}
              >
                导出 CSV
              </Button>
            )}
            {/*
              加入词库只在选中若干行后出现——没选行时它是个点了没有意义的按钮。
              掉榜行同样可选：它有 keyword_id，而「掉出去了想继续盯」正是加入词库
              最典型的动机。
            */}
            {access.canViewKeyword && selectedKeywordIds.length > 0 && (
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
            {/*
              查询失败时不显示条数，改为常驻的失败提示（issue #92）。此刻表格要么是空的、
              要么还是上一组条件的行，都不是当前条件的结果：显示「搜索结果：0」会把「没查成」
              读成「没命中」，留着旧条数则是拿旧数据冒充新数据。toast 几秒就没了，撑不住这个
              误读。重试不需要专门的入口——重新点「立即查询」/「开始筛选」就是重试。
            */}
            {query.requestFailed ? (
              <Typography.Text type="danger">
                查询失败，下表数据未更新，请检查网络后重新查询
              </Typography.Text>
            ) : (
              query.hasQueried && (
                <Typography.Text type="secondary">
                  搜索结果：
                  <Typography.Text strong>{query.resultTotal.toLocaleString()}</Typography.Text>
                </Typography.Text>
              )
            )}
            {/*
              筛选一生效，掉榜行必然全部消失——筛选以锚定日那一行为准，而掉榜行在那一天
              没有行可供匹配。不说出来的话，用户看到的是「加了个筛选，掉榜的词全没了」，
              会当成数据缺失去排查。读的是**生效条件**而不是草稿：草稿改了还没查时，
              表格里仍是上一组条件的结果，提示必须跟着数据走。
            */}
            {query.appliedHidesDropped && (
              <Tooltip title="筛选以所选日期那一行为准，而掉榜行在该日期没有数据，无法参与筛选">
                <Typography.Text type="warning">本次结果不含掉榜行</Typography.Text>
              </Tooltip>
            )}
          </Space>
        }
        request={(params) => query.runRequest(params)}
        // 失败已由全局 errorHandler 提示、由 `requestFailed` 常驻呈现。不传这个回调的话，
        // ProTable 会把同一个异常再抛一次（未处理的 rejection）。
        // 但**不能留空**：ProTable 在这里吞掉的是 request 里的一切异常，包括响应处理段的
        // 代码缺陷，那类异常没有 toast，留空就是零输出。记一笔 console.error 保住栈。
        onRequestError={(e) => console.error('[Analysis] 查询请求异常', e)}
        toolBarRender={() => [
          <Select
            key="sort-field"
            style={{ width: 140 }}
            value={query.sortDraft.field}
            onChange={(field) => query.setSortDraft((current) => ({ ...current, field }))}
            options={[
              { label: '当前排名', value: 'current_ranking' },
              { label: '排名变化', value: 'ranking_change' },
              { label: '历史排名', value: 'previous_ranking' },
              { label: '点击份额', value: 'top1_click_share' },
              { label: '转化份额', value: 'top1_conversion_share' },
            ]}
          />,
          <Select
            key="sort-direction"
            style={{ width: 100 }}
            value={query.sortDraft.dir}
            onChange={(dir) => query.setSortDraft((current) => ({ ...current, dir }))}
            options={[
              { label: '升序', value: 'asc' },
              { label: '降序', value: 'desc' },
            ]}
          />,
          <Button key="apply-sort" type="primary" onClick={query.applySort}>
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

      {trendTarget && (
        <KeywordTrendDrawer
          open={!!trendTarget}
          onClose={() => setTrendTarget(null)}
          keywordId={trendTarget.row.keyword_id}
          keyword={trendTarget.row.keyword}
          // 站点/视图/日期取点击那一刻冻结进 trendTarget 的值，不是活状态——见上方
          // trendTarget 的注释（PR #87 复审 P2）。
          site={trendTarget.site}
          view={trendTarget.view}
          on={trendTarget.date}
          canAddToLibrary={access.canViewKeyword}
        />
      )}
    </PageContainer>
  );
}
