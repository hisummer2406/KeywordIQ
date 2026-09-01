import { ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Alert, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import { useRef, useState } from 'react';
import { listNewArrivals, type NewArrivalRow } from '@/services/competitor';

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
 * **尚未实现：点开一行看详情**（独立站的变体/价格历史、Amazon 的采集元数据）。这不是
 * Amazon 分支特有的缺口——DTC 行同样没有这个交互，`competitor-storefront-watch` spec 里
 * 「查看条目详情」那条场景目前对两类来源都不成立，是遗留缺口，不在本变更范围内补。
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
        rowKey={(row) => `${row.source}:${row.source_key}:${row.product_key}`}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{ emptyText: '这段时间里，你关注的竞对没有上新' }}
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
            render: (_, row) => row.source_name || row.source_key,
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
    </>
  );
}
