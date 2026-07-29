import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { Card, Radio, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import {
  getCategories,
  getScope,
  searchAnalysis,
  type AnalysisRow,
  type CategoryOption,
  type View,
} from '@/services/analysis';
import { getSites, type SiteOption } from '@/services/upload';

/**
 * 分析查询屏（前端迁移阶段四）。作用域「站点→视图→日期」在表格之上，ProTable 消费
 * A 期 /api/analysis/search（筛选/排序/分页）+ /scope（日期选择器）+ /categories（类目下拉）。
 * 作用域用 ref 承载，供 request 闭包读取；切换任一维度 reload 回第 1 页。
 */
export default function AnalysisPage() {
  const actionRef = useRef<ActionType>();
  const scopeRef = useRef<{ site?: string; view: View; date?: string | null }>({ view: 'DAY' });

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

  const columns: ProColumns<AnalysisRow>[] = [
    { title: '排名', dataIndex: 'current_ranking', width: 80, sorter: true, search: false },
    { title: '关键词', dataIndex: 'keyword', ellipsis: true, copyable: true },
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
      title: '新词',
      dataIndex: 'is_new',
      width: 80,
      valueType: 'select',
      valueEnum: { true: { text: '新词' }, false: { text: '非新词' } },
      render: (_, r) => (r.is_new ? <Tag color="gold">新词</Tag> : '—'),
    },
    { title: '品牌', dataIndex: 'top1_brand', ellipsis: true },
    {
      title: '类目',
      dataIndex: 'top1_category',
      valueType: 'select',
      fieldProps: {
        showSearch: true,
        options: categories.map((c) => ({ label: `${c.category} (${c.count})`, value: c.category })),
      },
      render: (_, r) => r.top1_category || '—',
    },
    { title: 'ASIN', dataIndex: 'top1_asin', width: 120, copyable: true },
    { title: '商品标题', dataIndex: 'top1_title', ellipsis: true },
    {
      title: '点击份额',
      dataIndex: 'top1_click_share',
      width: 100,
      sorter: true,
      search: false,
      render: (_, r) => `${r.top1_click_share}%`,
    },
    {
      title: '转化份额',
      dataIndex: 'top1_conversion_share',
      width: 100,
      sorter: true,
      search: false,
      render: (_, r) => `${r.top1_conversion_share}%`,
    },
  ];

  return (
    <PageContainer title="分析查询">
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space size="large" wrap>
          <Space>
            <Typography.Text type="secondary">站点</Typography.Text>
            <Select
              style={{ width: 160 }}
              value={site}
              onChange={(v) => reloadScope(v, view)}
              options={sites.map((s) => ({ label: `${s.name_zh} (${s.code})`, value: s.code }))}
            />
          </Space>
          <Space>
            <Typography.Text type="secondary">视图</Typography.Text>
            <Radio.Group
              value={view}
              onChange={(e) => reloadScope(site, e.target.value)}
              optionType="button"
            >
              <Radio.Button value="DAY">日视图</Radio.Button>
              <Radio.Button value="WEEK">周视图</Radio.Button>
            </Radio.Group>
          </Space>
          <Space>
            <Typography.Text type="secondary">日期</Typography.Text>
            <Select
              style={{ width: 160 }}
              value={date ?? undefined}
              placeholder="无可用日期"
              onChange={onDateChange}
              options={availableDates.map((d) => ({ label: d, value: d }))}
              notFoundContent="该站点该视图暂无数据"
            />
          </Space>
        </Space>
      </Card>

      <ProTable<AnalysisRow>
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
          const res = await searchAnalysis({
            site: scope.site,
            view: scope.view,
            date: scope.date,
            page: params.current,
            perPage: params.pageSize,
            orderBy: sortField,
            orderDir: sortDir === 'ascend' ? 'asc' : sortDir === 'descend' ? 'desc' : undefined,
            keyword: params.keyword,
            brand: params.top1_brand,
            asin: params.top1_asin,
            product_title: params.top1_title,
            category_exact: params.top1_category,
            is_new: isNew,
          });
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
        }}
      />
    </PageContainer>
  );
}
