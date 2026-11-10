import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { useSearchParams } from '@umijs/max';
import { Alert, Card, Image, Radio, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { SiteCodeLabel } from '@/pages/Analysis/siteLabel';
import {
  listGroups,
  listNewArrivals,
  type CompetitorGroup,
  type NewArrivalFilters,
  type NewArrivalRow,
} from '@/services/competitor';
import { getSites, type SiteOption } from '@/services/upload';
import AsinDetailDrawer from './AsinDetailDrawer';
import ProductDetailDrawer from './ProductDetailDrawer';

/**
 * 竞对监控 · 商品发现（原「上新流」，#166 D6）：集中查看已关注店铺新出现的商品，方便看品。
 *
 * **筛选全部传到后端**，在两类来源合并之前分别施加——前端绝不在当前页上二次过滤，否则
 * 总数与页数会对不上。筛选写在 URL 里：从店铺管理点「看新商品」带着店铺过来，刷新也不丢。
 *
 * **两类来源的时间语义不同，界面必须分开说**：Shopify 有数据源给的上架时间，Amazon 只能是
 * 「我们首次见到它的时刻」——取决于何时采集到，可能晚好几天；首次漏采、之后补采到的老品
 * 也会以「首次发现于」出现在这里。把它说成上架时间，等于拿「我们发现得晚」冒充「他上得晚」。
 *
 * **主列表只放两类来源都有的字段**（图、标题、店铺、平台/站点、商品标识、时间、原链接）。
 * 缺图、缺标题、缺店名如实降级，不编造。点开一行看详情：Shopify 用 `ProductDetailDrawer`、
 * Amazon 用 `AsinDetailDrawer`，`detailRow` 是唯一的状态源。
 */

const SOURCE_META = {
  DTC: { color: 'geekblue', label: 'Shopify' },
  AMAZON: { color: 'orange', label: 'Amazon' },
} as const;

const DAY_OPTIONS = [
  { label: '近 7 天', value: 7 },
  { label: '近 30 天', value: 30 },
  { label: '近 90 天', value: 90 },
];

type SourceValue = 'ALL' | 'AMAZON' | 'DTC';

/** URL → 筛选。非法值一律落回缺省，不把一个看不懂的参数原样发给后端。 */
export function filtersFromParams(params: URLSearchParams): NewArrivalFilters {
  const days = Number(params.get('days'));
  const source = params.get('source');
  const groupId = Number(params.get('group'));
  return {
    days: [7, 30, 90].includes(days) ? days : 7,
    source: source === 'AMAZON' || source === 'DTC' ? source : undefined,
    site: params.get('site') || undefined,
    groupId: Number.isInteger(groupId) && groupId > 0 ? groupId : undefined,
    sellerId: params.get('seller') || undefined,
    domain: params.get('domain') || undefined,
  };
}

function paramsFromFilters(f: NewArrivalFilters): Record<string, string> {
  const out: Record<string, string> = {};
  if (f.days && f.days !== 7) out.days = String(f.days);
  if (f.source) out.source = f.source;
  if (f.site) out.site = f.site;
  if (f.groupId) out.group = String(f.groupId);
  if (f.sellerId) out.seller = f.sellerId;
  if (f.domain) out.domain = f.domain;
  return out;
}

function timeCell(row: NewArrivalRow) {
  const discovered = (
    <Tooltip title="我们首次见到这个商品的时刻，不是它真正的上架时间——取决于何时采集到；首次漏采、后来补采到的老商品也会出现在这里">
      <Typography.Text type="secondary">首次发现于</Typography.Text>
    </Tooltip>
  );
  return (
    <Space direction="vertical" size={0}>
      {row.source === 'DTC' && row.published_at ? (
        <Space size={4}>
          <Tooltip title="数据源直接给出的上架时间">
            <Typography.Text type="secondary">上架于</Typography.Text>
          </Tooltip>
          <span>{new Date(row.published_at).toLocaleString('zh-CN')}</span>
        </Space>
      ) : null}
      <Space size={4}>
        {discovered}
        <span>{new Date(row.discovered_at ?? row.at).toLocaleString('zh-CN')}</span>
      </Space>
    </Space>
  );
}

export default function CompetitorDiscoveryPage() {
  const actionRef = useRef<ActionType>();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = filtersFromParams(searchParams);
  const [detailRow, setDetailRow] = useState<NewArrivalRow | null>(null);
  const [groups, setGroups] = useState<CompetitorGroup[]>([]);
  const [sites, setSites] = useState<SiteOption[]>([]);

  useEffect(() => {
    listGroups()
      .then(setGroups)
      .catch(() => undefined);
    getSites()
      .then(setSites)
      .catch(() => undefined);
  }, []);

  // request 回调经 ref 读最新筛选：ProTable 不保证每次重查都换用最新一次渲染的 request。
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  // 筛选一变就回第 1 页重查。首次挂载由 ProTable 自己发请求，这里跳过，免得进屏查两遍。
  const filterKey = JSON.stringify(paramsFromFilters(filters));
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    actionRef.current?.reload(true);
  }, [filterKey]);

  const update = (patch: Partial<NewArrivalFilters>) =>
    setSearchParams(paramsFromFilters({ ...filters, ...patch }));

  const sourceValue: SourceValue = filters.source ?? 'ALL';
  const dtcRow = detailRow?.source === 'DTC' ? detailRow : null;
  const amazonRow = detailRow?.source === 'AMAZON' ? detailRow : null;
  const storeTag = filters.sellerId
    ? `Amazon 卖家 ${filters.sellerId}${filters.site ? `（${filters.site}）` : ''}`
    : filters.domain
      ? `Shopify 店铺 ${filters.domain}`
      : null;

  return (
    <PageContainer title="商品发现">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="这里只列出「纳入监控之后」新出现的商品"
        description="刚加入一家店时，它已有的存量商品不会涌进来。Amazon 的时间是我们首次发现它的时刻，取决于你何时采集，不是上架时间；Shopify 店铺另有数据源给的上架时间。"
      />

      <Card size="small" style={{ marginBottom: 12 }} styles={{ body: { padding: 12 } }}>
        <Space wrap>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={filters.days ?? 7}
            options={DAY_OPTIONS}
            onChange={(e) => update({ days: e.target.value as number })}
          />
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={sourceValue}
            options={[
              { label: '全部来源', value: 'ALL' },
              { label: 'Amazon', value: 'AMAZON' },
              { label: 'Shopify 店铺', value: 'DTC' },
            ]}
            onChange={(e) => {
              const value = e.target.value as SourceValue;
              // 切来源时清掉另一侧独有的条件：Shopify 没有站点和 Amazon 卖家，Amazon 没有店铺域名。
              // 后端在 UNION 前分侧施加筛选，留着另一侧的条件会让两侧都被排除、结果恒为空
              // （PR #174 Codex P2：从店铺管理「看新商品」进来后再切来源）。
              update(
                value === 'ALL'
                  ? { source: undefined }
                  : value === 'DTC'
                    ? { source: 'DTC', site: undefined, sellerId: undefined }
                    : { source: 'AMAZON', domain: undefined },
              );
            }}
          />
          <Select
            allowClear
            placeholder="Amazon 站点"
            style={{ width: 140 }}
            disabled={filters.source === 'DTC' || Boolean(filters.domain)}
            value={filters.site}
            onChange={(value) => update({ site: value })}
            options={sites.map((s) => ({ label: <SiteCodeLabel code={s.code} />, value: s.code }))}
          />
          <Select
            allowClear
            placeholder="全部分组"
            style={{ width: 180 }}
            value={filters.groupId}
            onChange={(value) => update({ groupId: value })}
            options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
          />
          {storeTag ? (
            <Tag closable onClose={() => update({ sellerId: undefined, domain: undefined })}>
              {storeTag}
            </Tag>
          ) : null}
        </Space>
      </Card>

      <ProTable<NewArrivalRow>
        actionRef={actionRef}
        rowKey={(row) => `${row.source}:${row.site ?? ''}:${row.source_key}:${row.product_key}`}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{ emptyText: '当前筛选条件下暂无新出现的商品' }}
        onRow={(row) => ({ onClick: () => setDetailRow(row), style: { cursor: 'pointer' } })}
        columns={[
          {
            title: '主图',
            dataIndex: 'image_url',
            width: 72,
            render: (_, row) =>
              row.image_url ? (
                <span onClick={(e) => e.stopPropagation()}>
                  <Image src={row.image_url} width={48} height={48} style={{ objectFit: 'contain' }} />
                </span>
              ) : (
                <Typography.Text type="secondary">无图</Typography.Text>
              ),
          },
          {
            title: '商品',
            dataIndex: 'title',
            ellipsis: true,
            render: (_, row) => (
              <Space direction="vertical" size={0}>
                {/* 标题缺失时后端已用商品标识兜底；这里不再自造文案 */}
                <Typography.Text ellipsis>{row.title || row.product_key}</Typography.Text>
                <Space size={8}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.source === 'AMAZON' ? `ASIN ${row.product_key}` : `商品 ID ${row.product_key}`}
                  </Typography.Text>
                  {row.product_url ? (
                    <a
                      href={row.product_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      style={{ fontSize: 12 }}
                    >
                      打开原商品
                    </a>
                  ) : null}
                </Space>
              </Space>
            ),
          },
          {
            title: '来源店铺',
            dataIndex: 'source_name',
            width: 220,
            ellipsis: true,
            render: (_, row) => {
              const meta = SOURCE_META[row.source] ?? SOURCE_META.DTC;
              return (
                <Space size={4}>
                  <Tag color={meta.color}>{meta.label}</Tag>
                  {row.site ? <SiteCodeLabel code={row.site} /> : null}
                  {/* 店名取不到时回落 seller_id/域名，不拿私有备注顶替 */}
                  <span>{row.source_name || row.source_key}</span>
                </Space>
              );
            },
          },
          { title: '时间', dataIndex: 'at', width: 260, render: (_, row) => timeCell(row) },
        ]}
        request={async (params) => {
          const res = await listNewArrivals({
            ...filtersRef.current,
            page: params.current,
            perPage: params.pageSize,
          });
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
        }}
      />

      <ProductDetailDrawer
        open={dtcRow !== null}
        domain={dtcRow?.source_key ?? null}
        productId={dtcRow ? Number(dtcRow.product_key) : null}
        title={dtcRow?.title ?? ''}
        onClose={() => setDetailRow(null)}
      />

      <AsinDetailDrawer
        open={amazonRow !== null}
        sellerId={amazonRow?.source_key ?? null}
        site={amazonRow?.site ?? null}
        asin={amazonRow?.product_key ?? null}
        title={amazonRow?.title ?? ''}
        imageUrl={amazonRow?.image_url ?? null}
        productUrl={amazonRow?.product_url ?? null}
        onClose={() => setDetailRow(null)}
      />
    </PageContainer>
  );
}
