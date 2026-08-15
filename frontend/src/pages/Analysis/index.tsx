import {
  DownOutlined,
  DownloadOutlined,
  FilterOutlined,
  ReloadOutlined,
  UpOutlined,
} from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
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
import CategoryPanel from './CategoryPanel';
import FilterPanel from './FilterPanel';
import ScopeBar from './ScopeBar';
import { buildColumns, totalColumnWidth } from './columns';
import { emptyFilters, toSearchBody, type ScopeDraft } from './filters';
import { readSnapshot, sameRequest, writeSlot, type RequestKey } from './stateCache';
import { useCachedState } from './useCachedState';

/**
 * 分析查询屏：作用域 / 过滤 / 结果三段。
 *
 * **草稿态与生效态是两份状态**，不是一份加脏标记。一旦条件与表格数据来自同一份状态，
 * 表格重渲染时就会读到尚未生效的条件，「当前显示的是哪组条件」无法从状态里读出来，
 * 导出（复用最近一次生效的查询体）也会跟着漂移。
 *
 * 查询显式触发；例外只有两类，各有理由：
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

export default function AnalysisPage() {
  const access = useAccess();
  const actionRef = useRef<ActionType>();

  /**
   * 挂载时读一次快照。它同时决定两件事：各 ref 的初值，以及**这次是「复原」还是
   * 「首次进入」**——后者是挂载副作用那道闸的判据。用惰性 useState 而不是直接调用，
   * 保证整个生命周期只读一次；之后所有写入都走 write-through。
   */
  const [initialSnapshot] = useState(() => readSnapshot());
  const restored = initialSnapshot !== null;

  // 草稿态：用户正在编辑
  const [scope, setScope] = useCachedState('scope', { view: 'DAY' } as ScopeDraft);
  const [filters, setFilters] = useCachedState('filters', emptyFilters);

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
  const [filterOpen, setFilterOpen] = useCachedState('filterOpen', false);
  const [resultTotal, setResultTotal] = useCachedState('resultTotal', 0);
  // 总数缓存与"下次要重算"的标记。ProTable 的 request 同时承担翻页与条件变更两种触发，
  // 靠这个标记把两者区分开——只有条件变更那次才让后端付精确计数的代价。
  const totalRef = useRef(initialSnapshot?.total ?? 0);
  const needsTotalRef = useRef(initialSnapshot?.needsTotal ?? true);
  const [pendingRebuild, setPendingRebuild] = useCachedState('pendingRebuild', false);
  const [sortDraft, setSortDraft] = useCachedState('sortDraft', {
    field: 'current_ranking',
    dir: 'asc' as 'asc' | 'desc',
  });
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

  const draftBody = useMemo(() => toSearchBody(scope, filters), [scope, filters]);
  const draftSig = useMemo(() => JSON.stringify(draftBody), [draftBody]);
  const dirty = draftSig !== appliedSig;

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

  const submitScope = () => {
    const scopeOnly = toSearchBody(scope, { ...emptyFilters(), q: filters.q });
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

  const resetFilters = () => setFilters((current) => ({ ...emptyFilters(), q: current.q }));

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
  const reloadScopeMeta = async (nextSite: string | undefined, nextView: View) => {
    const s = await getScope(nextSite, nextView);
    setAvailableDates(s.available_dates);
    setScope({ site: s.site, view: nextView, date: s.latest_date });
    // brands 一并清：品牌筛选面板暂时下线，但字段仍在模型里，留着不清会成为
    // 一个界面上看不见却在生效的条件
    setFilters((f) => ({ ...f, categories: [], brands: [] }));
    if (appliedRef.current) {
      appliedRef.current = { ...appliedRef.current, categories: undefined, brands: undefined };
      writeSlot({ applied: appliedRef.current });
    }
    reloadCategories(s.site, nextView, s.latest_date);
    return s;
  };

  useEffect(() => {
    // 从缓存复原：整段初始化跳过，一个请求都不发。表格那次 request 会命中短路分支，
    // 拿缓存行渲染。**只跳过挂载初始化**——用户动作触发的请求（改站点/视图重拉
    // scope 与 categories、点「立即查询」）一律照旧。
    if (restored) return;
    getSites()
      .then(setSites)
      .catch(() => undefined);
    // 首屏：拉作用域后立即查一次，用户不必先点一下才看到数据
    reloadScopeMeta(undefined, 'DAY')
      .then((s) => {
        const body = toSearchBody(
          { site: s.site, view: 'DAY', date: s.latest_date },
          emptyFilters(),
        );
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
              <Typography.Text strong>搜索结果过滤</Typography.Text>
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
              <Col flex="260px">
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
            <div
              style={{
                margin: '20px -24px -12px',
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
        // 给定总宽 → AntD 切到 table-layout: fixed，列上的 width 才真正生效。
        // 不给的话是 auto 布局，width 只是建议值，浏览器按内容抢宽度——表现为
        // "同样写 130px 的两列渲染出来不一样宽"。窗口比总宽窄时横向滚动。
        scroll={{ x: totalColumnWidth(appliedView) }}
        // 筛选全部在上面的过滤区，表格不再自带条件表单
        search={false}
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
            <Typography.Text type="secondary">
              搜索结果：<Typography.Text strong>{resultTotal.toLocaleString()}</Typography.Text>
            </Typography.Text>
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
    </PageContainer>
  );
}
