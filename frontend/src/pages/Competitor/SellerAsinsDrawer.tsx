import { ProTable } from '@ant-design/pro-components';
import { Alert, Drawer, Image, Space, Tag, Typography } from 'antd';
import { listSellerAsins, type SellerAsinRow, type WatchRow } from '@/services/competitor';
import CollectGuide from './CollectGuide';
import { sellerDisplayName } from './columns';

/**
 * 店铺管理点开一个 Amazon 卖家：已采集到的商品（#166 D3）。
 *
 * **只读现有共享观测**，不新建产品库。`total` 是「已采集到」的条数，**不是这家店的全部在售
 * 商品**——Amazon 侧是被动采集，覆盖多少取决于运营翻了几页，没有完整性依据。
 *
 * 「基线」标记：首次采集时就已存在的那一批，不计入新发现；其余是之后新出现的。
 */
export default function SellerAsinsDrawer({
  watch,
  domain,
  onClose,
}: {
  watch: WatchRow | null;
  /** 该站点的 Amazon 域名（如 amazon.co.uk），用于拼商品链接；取不到时不出链接。 */
  domain?: string;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={watch ? `${sellerDisplayName(watch)} · 已采集的商品` : ''}
      open={watch !== null}
      size={760}
      onClose={onClose}
      destroyOnHidden
    >
      {watch && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="这里是已经采集到的商品，不是这家店的全部在售商品"
            description={<CollectGuide />}
          />
          <ProTable<SellerAsinRow>
            rowKey="asin"
            search={false}
            options={false}
            pagination={{ pageSize: 50, showSizeChanger: false }}
            locale={{ emptyText: '还没有采集到任何商品' }}
            request={async (params) => {
              const res = await listSellerAsins(watch.watch_id, {
                page: params.current,
                perPage: params.pageSize,
              });
              return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
            }}
            columns={[
              {
                title: '主图',
                dataIndex: 'image_url',
                width: 72,
                render: (_, row) =>
                  row.image_url ? (
                    <Image src={row.image_url} width={48} height={48} style={{ objectFit: 'contain' }} />
                  ) : (
                    <Typography.Text type="secondary">无图</Typography.Text>
                  ),
              },
              {
                title: '商品',
                dataIndex: 'title',
                ellipsis: true,
                render: (_, row) => (
                  <Space orientation="vertical" size={0}>
                    <Typography.Text>{row.title || row.asin}</Typography.Text>
                    {domain ? (
                      <a href={`https://www.${domain}/dp/${row.asin}`} target="_blank" rel="noreferrer">
                        {row.asin}
                      </a>
                    ) : (
                      <Typography.Text type="secondary">{row.asin}</Typography.Text>
                    )}
                  </Space>
                ),
              },
              {
                title: '首次发现于',
                dataIndex: 'first_seen_at',
                width: 200,
                render: (_, row) => (
                  <Space size={4}>
                    <span>{new Date(row.first_seen_at).toLocaleString('zh-CN')}</span>
                    {row.is_baseline ? (
                      <Tag title="首次采集时就已存在，不计入新发现">基线</Tag>
                    ) : null}
                  </Space>
                ),
              },
            ]}
          />
        </>
      )}
    </Drawer>
  );
}
