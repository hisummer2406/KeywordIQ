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
import { Button, Card, Col, Row, Select, Space, Typography, message } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  exportAnalysis,
  getBrands,
  getCategories,
  getScope,
  searchAnalysis,
  type AnalysisRow,
  type BrandOption,
  type CategoryOption,
  type SearchBody,
  type View,
} from '@/services/analysis';
import { getSites, type SiteOption } from '@/services/upload';
import BrandPanel from './BrandPanel';
import CategoryPanel from './CategoryPanel';
import FilterPanel from './FilterPanel';
import ScopeBar from './ScopeBar';
import { buildColumns } from './columns';
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
  const [brands, setBrands] = useState<BrandOption[]>([]);
  const [exporting, setExporting] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [resultTotal, setResultTotal] = useState(0);
  const [sortDraft, setSortDraft] = useState({
    field: 'current_ranking',
    dir: 'asc' as 'asc' | 'desc',
  });
  const appliedSortRef = useRef(sortDraft);

  const draftBody = useMemo(() => toSearchBody(scope, filters), [scope, filters]);
  const draftSig = useMemo(() => JSON.stringify(draftBody), [draftBody]);
  const dirty = draftSig !== appliedSig;

  /** 把当前草稿设为生效条件并重查（回第 1 页）。 */
  const apply = (body: SearchBody, sig: string) => {
    appliedRef.current = body;
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
    setFilters((f) => ({ ...f, categories: [], brands: [] }));
    if (appliedRef.current) {
      appliedRef.current = { ...appliedRef.current, categories: undefined, brands: undefined };
    }
    getCategories(s.site, nextView)
      .then(setCategories)
      .catch(() => setCategories([]));
    getBrands(s.site, nextView)
      .then(setBrands)
      .catch(() => setBrands([]));
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

  const columns = useMemo(() => buildColumns(appliedView), [appliedView]);

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
          <Button
            type="link"
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
                <div style={{ marginTop: 16 }}>
                  <Typography.Text strong>选择筛选品牌</Typography.Text>
                  <BrandPanel
                    options={brands}
                    value={filters.brands}
                    onChange={(brands) => setFilters((f) => ({ ...f, brands }))}
                  />
                </div>
              </Col>
              <Col flex="auto" style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 16 }}>
                <FilterPanel value={filters} onChange={setFilters} />
              </Col>
            </Row>
            <div
              style={{
                margin: '20px -12px -12px',
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

      <ProTable<AnalysisRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="keyword_id"
        columns={columns}
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
          </Space>
        }
        request={async (params) => {
          const body = appliedRef.current;
          if (!body?.date) return { data: [], total: 0, success: true };
          // 排序只有工具栏一个入口（列上没有 sorter），故不读 ProTable 的 sort 参数——
          // 读了也永远是空对象，那种分支看着像在工作，实际是死代码。
          const res = await searchAnalysis({
            ...body,
            orderBy: appliedSortRef.current.field,
            orderDir: appliedSortRef.current.dir,
            page: params.current,
            perPage: params.pageSize,
          });
          setResultTotal(res.total ?? 0);
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
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
