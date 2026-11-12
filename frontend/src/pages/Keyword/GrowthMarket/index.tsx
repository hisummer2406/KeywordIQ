import { SearchOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Alert, Button, Card, Select, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { toSiteSelectOptions } from '@/pages/Analysis/siteLabel';
import {
  getGrowthScope,
  searchGrowth,
  type GrowthRow,
  type GrowthScopeInfo,
} from '@/services/keyword';
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
 *
 * **首屏不自动查询榜单**（与分析屏相反，那一屏是进去就查）。这一屏的默认锚定日多半不是
 * 用户想看的那一天：榜单按锚定日切片，而最新一期恰恰最可能因窗口内缺期或口径过严而为空。
 * 先查一次既浪费一次请求，又让用户在还没做任何选择时就看到一张空表。
 *
 * 代价是空表格从两种成因变成三种（还没查 / 窗口缺期 / 口径下确实没命中），三者含义完全
 * 不同却长得一样。所以 `hasQueried` 同时管住三处：条数显隐、dirty 标签、空状态文案。
 * 少任何一处，「还没查」都会伪装成「今天没有增长词」。
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

  /**
   * 是否已经查过一次。`appliedSig` 只在 `apply()` 里被写非空值，所以它就是这个判据。
   * 首屏不自动查询，故它开局为假，管住下面三处（见文件头注释）。
   */
  const hasQueried = appliedSig !== '';
  /**
   * 「条件已修改」提示。**没查过就不提示**——首屏生效态本来就是空的，判据必然为真，
   * 而那时用户一个条件都没改，那句话是错的；该看的引导是表格空状态里那句「点立即查询」。
   */
  const dirty = hasQueried && JSON.stringify(draft) !== appliedSig;

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
    // 首屏**只拉作用域**（站点、可选日期、判定口径）——它们是选择器与口径说明条渲染的
    // 前提，不拉的话用户连日期都选不了。榜单本身不查，等用户点「立即查询」。
    //
    // 表格在挂载时仍会调一次 request，但 `appliedRef.current` 是 null，那里直接返回
    // 空结果、不发请求（见 request 开头）。
    reloadScope().catch(() => undefined);
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
            options={toSiteSelectOptions(sites)}
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
          title="窗口内有期次缺少数据"
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
        // 空表格有三种含义（还没查 / 窗口缺期 / 口径下没命中），这里区分第一种。
        // 缺期那种由上面的 Alert 承担。
        locale={
          hasQueried
            ? undefined
            : { emptyText: '选好站点与锚定日后，点「立即查询」查看榜单' }
        }
        headerTitle={
          // 没查过就不显示条数。显示「持续增长词：0」会被读成「今天没有增长词」，
          // 而实际是还没查——这一屏的空结果本就有两种正当成因，再混进第三种，
          // 用户没有任何办法分辨。
          hasQueried ? (
            <Typography.Text type="secondary">
              持续增长词：<Typography.Text strong>{total.toLocaleString()}</Typography.Text>
            </Typography.Text>
          ) : undefined
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
