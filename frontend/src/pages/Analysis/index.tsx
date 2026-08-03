import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { DownloadOutlined } from '@ant-design/icons';
import { useAccess } from '@umijs/max';
import { Button, Radio, Select, Space, Tag, Typography, message } from 'antd';
import { useEffect, useRef, useState } from 'react';
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
import TrendSparkline from '@/components/TrendSparkline';
import ShareBar from '@/components/ShareBar';

/**
 * 分析查询屏（前端迁移阶段四）。作用域「站点→视图→日期」在表格之上，ProTable 消费
 * A 期 /api/analysis/search（筛选/排序/分页）+ /scope（日期选择器）+ /categories（类目下拉）。
 * 作用域用 ref 承载，供 request 闭包读取；切换任一维度 reload 回第 1 页。
 */
export default function AnalysisPage() {
  const access = useAccess();
  const actionRef = useRef<ActionType>();
  const scopeRef = useRef<{ site?: string; view: View; date?: string | null }>({ view: 'DAY' });
  // 表格最近一次查询的请求体，导出按它复现「用户当下看到的这一份」。分页字段导出时剔除。
  const lastQueryRef = useRef<SearchBody | null>(null);

  const [exporting, setExporting] = useState(false);
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [site, setSite] = useState<string>();
  const [view, setView] = useState<View>('DAY');
  const [date, setDate] = useState<string | null>();
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [categories, setCategories] = useState<CategoryOption[]>([]);

  // 加载作用域：拉可选日期 + 默认最新 + 类目，然后 reload 表格
  const reloadScope = async (nextSite: string | undefined, nextView: View) => {
    const s = await getScope(nextSite, nextView);
    setSite(s.site);
    setView(nextView);
    setAvailableDates(s.available_dates);
    setDate(s.latest_date);
    scopeRef.current = { site: s.site, view: nextView, date: s.latest_date };
    getCategories(s.site, nextView).then(setCategories).catch(() => undefined);
    actionRef.current?.reload();
  };

  useEffect(() => {
    getSites().then(setSites).catch(() => undefined);
    reloadScope(undefined, 'DAY').catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onDateChange = (d: string) => {
    setDate(d);
    scopeRef.current = { ...scopeRef.current, date: d };
    actionRef.current?.reload();
  };

  const onExport = async () => {
    const scopedFilters = lastQueryRef.current;
    if (!scopedFilters) return;
    setExporting(true);
    try {
      await exportAnalysis(scopedFilters);
    } catch (e: any) {
      // 413（超出行数上限）在这里以后端原文呈现，提示用户加筛选而不是给一份截断数据
      message.error(e?.message || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const columns: ProColumns<AnalysisRow>[] = [
    { title: '排名', dataIndex: 'current_ranking', width: 80, sorter: true, search: false },
    // 关键词与商品标题是两列「要读内容」的列，其余自适应列给定宽，把剩余横向空间
    // 让给这两列。此前四列自适应各分到约 200px，商品标题几乎必被截断。
    { title: '关键词', dataIndex: 'keyword', width: 220, ellipsis: true, copyable: true },
    {
      title: '7天走势',
      dataIndex: 'trend',
      width: 90,
      search: false,
      hideInTable: view !== 'DAY',
      render: (_, r) => <TrendSparkline values={r.trend} />,
    },
    {
      title: '排名变化',
      dataIndex: 'ranking_change',
      width: 100,
      sorter: true,
      search: false,
      render: (_, r) => {
        if (r.ranking_change === 0) return <span style={{ color: '#999' }}>0</span>;
        const up = r.ranking_change < 0; // 排名数字变小 = 上升
        return (
          <span style={{ color: up ? '#3f8600' : '#cf1322' }}>
            {up ? '↑' : '↓'} {Math.abs(r.ranking_change)}
          </span>
        );
      },
    },
    {
      title: '周排名变化',
      dataIndex: 'week_change',
      width: 100,
      search: false,
      hideInTable: view !== 'DAY',
      render: (_, r) => {
        if (r.week_change === null || r.week_change === undefined)
          return <span style={{ color: '#ccc' }}>—</span>;
        if (r.week_change === 0) return <span style={{ color: '#999' }}>0</span>;
        const up = r.week_change < 0;
        return (
          <span style={{ color: up ? '#3f8600' : '#cf1322' }}>
            {up ? '↑' : '↓'} {Math.abs(r.week_change)}
          </span>
        );
      },
    },
    {
      title: '新词',
      dataIndex: 'is_new',
      width: 80,
      valueType: 'select',
      valueEnum: { true: { text: '新词' }, false: { text: '非新词' } },
      render: (_, r) => (r.is_new ? <Tag color="gold">新词</Tag> : '—'),
    },
    { title: '品牌', dataIndex: 'top1_brand', width: 130, ellipsis: true },
    {
      title: '类目',
      dataIndex: 'top1_category',
      width: 150,
      ellipsis: true,
      valueType: 'select',
      fieldProps: {
        showSearch: true,
        options: categories.map((c) => ({ label: `${c.category} (${c.count})`, value: c.category })),
      },
      render: (_, r) => r.top1_category || '—',
    },
    { title: 'ASIN', dataIndex: 'top1_asin', width: 120, copyable: true },
    // 不给 width：让它吃掉所有剩余横向空间。ellipsis 仍保留——再宽也有超长标题，
    // 截断时 antd 会挂 title 属性，悬停可看全文。
    { title: '商品标题', dataIndex: 'top1_title', ellipsis: true },
    {
      title: '点击份额',
      dataIndex: 'top1_click_share',
      width: 110,
      sorter: true,
      search: false,
      render: (_, r) => <ShareBar value={r.top1_click_share} />,
    },
    {
      title: '转化份额',
      dataIndex: 'top1_conversion_share',
      width: 110,
      sorter: true,
      search: false,
      render: (_, r) => <ShareBar value={r.top1_conversion_share} />,
    },
  ];

  return (
    <PageContainer title="ABA数据选品">
      <ProTable<AnalysisRow>
        /**
         * 作用域三元组（站点 / 视图 / 日期）放进表格工具栏，与导出、刷新同一行。
         *
         * 此前它们独占一张 Card，加上下边距约吃掉 90px 竖向空间。作用域与下面的筛选
         * 条件性质不同——它决定「查哪一份数据切片」，不是「筛哪些行」——但对用户而言
         * 都是"查询前要设的东西"，挤在一行反而更接近数据工具的习惯。
         */
        headerTitle={
          <Space size="middle" wrap>
            <Space size={6}>
              <Typography.Text type="secondary">站点</Typography.Text>
              <Select
                size="small"
                style={{ width: 150 }}
                value={site}
                onChange={(v) => reloadScope(v, view)}
                options={sites.map((s) => ({ label: `${s.name_zh} (${s.code})`, value: s.code }))}
              />
            </Space>
            <Radio.Group
              size="small"
              value={view}
              onChange={(e) => reloadScope(site, e.target.value)}
              optionType="button"
            >
              <Radio.Button value="DAY">日视图</Radio.Button>
              <Radio.Button value="WEEK">周视图</Radio.Button>
            </Radio.Group>
            <Space size={6}>
              <Typography.Text type="secondary">日期</Typography.Text>
              <Select
                size="small"
                style={{ width: 140 }}
                value={date ?? undefined}
                placeholder="无可用日期"
                onChange={onDateChange}
                options={availableDates.map((d) => ({ label: d, value: d }))}
                notFoundContent="该站点该视图暂无数据"
              />
            </Space>
          </Space>
        }
        actionRef={actionRef}
        rowKey="keyword_id"
        columns={columns}
        search={{ labelWidth: 'auto' }}
        pagination={{ pageSize: 50, showSizeChanger: true }}
        dateFormatter="string"
        options={{ reload: true, density: false, setting: true }}
        request={async (params, sort) => {
          const scope = scopeRef.current;
          if (!scope.date) return { data: [], total: 0, success: true };
          const [sortField, sortDir] = Object.entries(sort ?? {})[0] ?? [];
          const isNew =
            params.is_new === undefined
              ? undefined
              : params.is_new === 'true' || params.is_new === true;
          // 作用域 + 筛选 + 排序，不含分页——导出直接复用它，导的是整个作用域而非当前页
          const scopedFilters: SearchBody = {
            site: scope.site,
            view: scope.view,
            date: scope.date,
            orderBy: sortField,
            orderDir: sortDir === 'ascend' ? 'asc' : sortDir === 'descend' ? 'desc' : undefined,
            keyword: params.keyword,
            brand: params.top1_brand,
            asin: params.top1_asin,
            product_title: params.top1_title,
            category_exact: params.top1_category,
            is_new: isNew,
          };
          lastQueryRef.current = scopedFilters;
          const res = await searchAnalysis({
            ...scopedFilters,
            page: params.current,
            perPage: params.pageSize,
          });
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
        }}
        toolBarRender={() =>
          access.canExport
            ? [
                <Button
                  key="export"
                  icon={<DownloadOutlined />}
                  loading={exporting}
                  disabled={!date}
                  onClick={onExport}
                >
                  导出 CSV
                </Button>,
              ]
            : []
        }
      />
    </PageContainer>
  );
}
