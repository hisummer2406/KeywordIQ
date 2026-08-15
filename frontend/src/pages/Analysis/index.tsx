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
import FilterPanel, { LABEL_WIDTH } from './FilterPanel';
import ScopeBar from './ScopeBar';
import { buildColumns, totalColumnWidth } from './columns';
import { emptyFilters, toSearchBody, type FilterDraft, type ScopeDraft } from './filters';

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

  // 草稿态：用户正在编辑
  const [scope, setScope] = useState<ScopeDraft>({ view: 'DAY' });
  const [filters, setFilters] = useState<FilterDraft>(emptyFilters);

  // 生效态：表格 request 实际读的那份。用 ref 承载，供 request 闭包读取。
  const appliedRef = useRef<SearchBody | null>(null);
  // 生效态的视图单独提出来做 state：列集合（走势/周变化）随它变，需要触发重渲染
  const [appliedView, setAppliedView] = useState<View>('DAY');
  // 生效条件的签名，与草稿签名比较得出 dirty，不额外维护脏标记
  const [appliedSig, setAppliedSig] = useState<string>('');

  const [sites, setSites] = useState<SiteOption[]>([]);
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [exporting, setExporting] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [resultTotal, setResultTotal] = useState(0);
  // 总数缓存与"下次要重算"的标记。ProTable 的 request 同时承担翻页与条件变更两种触发，
  // 靠这个标记把两者区分开——只有条件变更那次才让后端付精确计数的代价。
  const totalRef = useRef(0);
  const needsTotalRef = useRef(true);
  const [pendingRebuild, setPendingRebuild] = useState(false);
  const [sortDraft, setSortDraft] = useState({
    field: 'current_ranking',
    dir: 'asc' as 'asc' | 'desc',
  });
  const appliedSortRef = useRef(sortDraft);

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
    }
    reloadCategories(s.site, nextView, s.latest_date);
    return s;
  };

  useEffect(() => {
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
        pagination={{ pageSize: 50, showSizeChanger: true }}
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
          return { data: res.data ?? [], total, success: res.success !== false };
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
