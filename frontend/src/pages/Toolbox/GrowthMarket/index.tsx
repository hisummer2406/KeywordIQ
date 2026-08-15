import { SearchOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Alert, Button, Card, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { formatSiteLabel } from '@/pages/Analysis/siteLabel';
import {
  getGrowthScope,
  searchGrowth,
  type GrowthRow,
  type GrowthScopeInfo,
} from '@/services/toolbox';
import { getSites, type SiteOption } from '@/services/upload';
import { buildColumns } from './columns';
import { formatCriteria, formatMissingDates, toSortParams } from './criteria';

/**
 * 持续增长市场：近 N 天名次持续前进且幅度达标的关键词。
 *
 * 与分析屏同一条纪律：**草稿态与生效态是两份状态**。改站点或日期只进草稿，点「立即
 * 查询」才生效——否则「表格现在显示的是哪个站点/哪一天」无法从状态里读出来。
 * 排序与翻页例外，走 ProTable 自身的 request 即时生效（它们作用于已确定的结果集）。
 *
 * 改站点会立即重拉 `/scope`（小表），但**不重查表格**：否则用户会在一份属于上一个站点
 * 的日期清单里做选择。
 */
export default function GrowthMarketPage() {
  const actionRef = useRef<ActionType>();

  // 草稿态
  const [draft, setDraft] = useState<{ site?: string; date?: string | null }>({});
  // 生效态：表格 request 读这一份
  const appliedRef = useRef<{ site?: string; date?: string | null } | null>(null);
  const [appliedSig, setAppliedSig] = useState('');

  const [sites, setSites] = useState<SiteOption[]>([]);
  const [scope, setScope] = useState<GrowthScopeInfo | null>(null);
  const [missing, setMissing] = useState<string[]>([]);
  const [total, setTotal] = useState(0);

  const dirty = JSON.stringify(draft) !== appliedSig;

  const apply = (next: { site?: string; date?: string | null }) => {
    appliedRef.current = next;
    setAppliedSig(JSON.stringify(next));
    actionRef.current?.reload(true);
  };

  const reloadScope = async (site?: string) => {
    const s = await getGrowthScope(site);
    setScope(s);
    setDraft({ site: s.site, date: s.latest_date });
    return s;
  };

  useEffect(() => {
    getSites()
      .then(setSites)
      .catch(() => undefined);
    // 首屏拉完作用域立即查一次，用户不必先点一下才看到数据
    reloadScope()
      .then((s) => apply({ site: s.site, date: s.latest_date }))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSiteChange = (site: string) => {
    // 日期清单按站点取，换站点必须重拉；但不重查表格——查询由「立即查询」触发
    reloadScope(site).catch(() => undefined);
  };

  const missingText = formatMissingDates(missing);

  return (
    <PageContainer title="持续增长市场">
      <Card size="small" styles={{ body: { padding: 16 } }}>
        <Space wrap>
          <Select
            style={{ width: 200 }}
            value={draft.site}
            placeholder="站点"
            onChange={onSiteChange}
            options={sites.map((site) => ({ label: formatSiteLabel(site), value: site.code }))}
          />
          <Select
            style={{ width: 160 }}
            value={draft.date ?? undefined}
            placeholder="无可用日期"
            onChange={(date) => setDraft((d) => ({ ...d, date }))}
            options={(scope?.available_dates ?? []).map((date) => ({ label: date, value: date }))}
            notFoundContent="该站点暂无日报数据"
          />
          <Button
            type="primary"
            icon={<SearchOutlined />}
            disabled={!draft.date}
            onClick={() => apply(draft)}
          >
            立即查询
          </Button>
          {dirty && <Tag color="orange">条件已修改，点击「立即查询」生效</Tag>}
        </Space>

        {scope && (
          <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
            口径：{formatCriteria(scope)}。仅日报，周报没有「连续 {scope.window_days} 天」这个概念。
          </Typography.Paragraph>
        )}
      </Card>

      {missingText && (
        <Alert
          style={{ marginTop: 12 }}
          type="warning"
          showIcon
          message="窗口内有期次缺少数据"
          description={missingText}
        />
      )}

      <ProTable<GrowthRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="keyword_id"
        columns={buildColumns()}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 50, showSizeChanger: true }}
        headerTitle={
          <Typography.Text type="secondary">
            持续增长词：<Typography.Text strong>{total.toLocaleString()}</Typography.Text>
          </Typography.Text>
        }
        request={async (params, sort) => {
          const applied = appliedRef.current;
          if (!applied?.date) return { data: [], total: 0, success: true };
          const res = await searchGrowth({
            site: applied.site,
            date: applied.date,
            page: params.current,
            perPage: params.pageSize,
            ...toSortParams(sort),
          });
          setMissing(res.missing_dates ?? []);
          setTotal(res.total ?? 0);
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
        }}
      />
    </PageContainer>
  );
}
