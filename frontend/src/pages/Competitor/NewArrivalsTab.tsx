import { ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Alert, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import { useRef, useState } from 'react';
import { siteCodeToFlag } from '@/pages/Analysis/siteLabel';
import { listNewArrivals, type NewArrivalRow } from '@/services/competitor';
import AsinDetailDrawer from './AsinDetailDrawer';
import ProductDetailDrawer from './ProductDetailDrawer';

/**
 * 上新流：两类来源合并成一个流。
 *
 * **两类来源的时间语义不同，界面必须分开说。** 独立站的时间是数据源给的真实上架时间
 * （精确到分钟），Amazon 侧只能是「我们首次见到它的时刻」——取决于何时采集到，可能
 * 晚好几天。用同一个措辞呈现，等于拿「我们发现得晚」冒充「他上得晚」。
 *
 * **主列表只放两类来源的交集字段**（来源、店铺/卖家、商品、时间）。独立站独有的变体
 * 与价格、Amazon 独有的采集元数据都不进这张表——否则会有一半的列对另一类来源恒为空。
 *
 * 两类来源都是真实数据（`store_crud.new_arrivals_stmt` 的 UNION ALL）——这个组件在
 * Amazon 侧还没有数据时就已经按两类形状写好，接入时零改动，本身就是这个形状设计对了
 * 的证据。
 *
 * **点开一行看详情**：两类来源都已实现，但数据形状完全不同，各用一个组件——
 * `ProductDetailDrawer`（独立站：变体价格与店内名次历史，见该组件顶部为什么选表格
 * 不选图表）、`AsinDetailDrawer`（Amazon：首次发现时刻 + 卖家级近似的最近采集时刻，
 * 见该组件顶部为什么后者不是这个 ASIN 专属的）。同一时刻只会有一个抽屉打开——
 * `detailRow` 是唯一的状态源，由它的 `source` 决定渲染哪一个。
 */

const TIME_KIND_META = {
  PUBLISHED: {
    label: '上架于',
    hint: '数据源直接给出的上架时间，精确到分钟。',
  },
  DISCOVERED: {
    label: '发现于',
    hint: '我们首次见到这个商品的时刻，不是它真正的上架时间——取决于何时采集到，可能晚几天。',
  },
} as const;

const SOURCE_META = {
  DTC: { color: 'geekblue', label: '独立站' },
  AMAZON: { color: 'orange', label: '亚马逊' },
} as const;

export default function NewArrivalsTab() {
  const actionRef = useRef<ActionType>();
  const [days, setDays] = useState(7);
  const [detailRow, setDetailRow] = useState<NewArrivalRow | null>(null);

  const dtcRow = detailRow?.source === 'DTC' ? detailRow : null;
  const amazonRow = detailRow?.source === 'AMAZON' ? detailRow : null;

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="这里只统计「纳入监控之后」新出现的商品"
        description="刚加入一家店时，它已有的存量商品不会涌进这个流——那是「我们刚开始看」，不是「今天上新」。想看某家店的历史上架情况，请到独立站清单里点开它的在售商品。"
      />

      <Space style={{ marginBottom: 12 }}>
        <Typography.Text>时间范围</Typography.Text>
        <Segmented
          value={days}
          onChange={(value) => {
            setDays(value as number);
            actionRef.current?.reload(true);
          }}
          options={[
            { label: '近 7 天', value: 7 },
            { label: '近 30 天', value: 30 },
            { label: '近 90 天', value: 90 },
          ]}
        />
      </Space>

      <ProTable<NewArrivalRow>
        actionRef={actionRef}
        // 共享层的真实粒度是 (seller_id, site, asin)：同一 seller_id 跨站点卖同一 ASIN
        // 是常态，rowKey 不带 site 会让两行撞成一个 key，React 与分页都会认错行。
        rowKey={(row) => `${row.source}:${row.site ?? ''}:${row.source_key}:${row.product_key}`}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        // 空流只能证明「已采集范围内没有新发现/新上架」，不能证明真的没有——尤其是
        // Amazon 侧，被动采集的覆盖率取决于运营的浏览习惯。措辞不承诺没有真实新品。
        locale={{ emptyText: '当前时间范围内暂无上架/新发现记录' }}
        // 两类来源都可点开——具体渲染哪个抽屉由 detailRow.source 决定，见组件顶部注释。
        onRow={(row) => ({ onClick: () => setDetailRow(row), style: { cursor: 'pointer' } })}
        columns={[
          {
            title: '来源',
            dataIndex: 'source',
            width: 90,
            render: (_, row) => {
              const meta = SOURCE_META[row.source] ?? SOURCE_META.DTC;
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: '店铺 / 卖家',
            dataIndex: 'source_name',
            width: 200,
            ellipsis: true,
            render: (_, row) => (
              <Space size={4}>
                {row.site ? <span>{siteCodeToFlag(row.site)}</span> : null}
                <span>{row.source_name || row.source_key}</span>
              </Space>
            ),
          },
          { title: '商品', dataIndex: 'title', ellipsis: true },
          {
            title: '时间',
            dataIndex: 'at',
            width: 230,
            render: (_, row) => {
              const meta = TIME_KIND_META[row.time_kind] ?? TIME_KIND_META.DISCOVERED;
              return (
                <Space size={4}>
                  <Tooltip title={meta.hint}>
                    <Typography.Text type="secondary">{meta.label}</Typography.Text>
                  </Tooltip>
                  <span>{new Date(row.at).toLocaleString('zh-CN')}</span>
                </Space>
              );
            },
          },
        ]}
        request={async (params) => {
          const res = await listNewArrivals({
            days,
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
        onClose={() => setDetailRow(null)}
      />
    </>
  );
}
