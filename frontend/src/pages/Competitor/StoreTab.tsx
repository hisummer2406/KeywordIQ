import { ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Alert, Button, Drawer, Form, Input, Modal, Select, Space, Table, message } from 'antd';
import { useRef, useState } from 'react';
import {
  addStore,
  listStoreProducts,
  listStores,
  removeStore,
  updateStore,
  type CompetitorGroup,
  type StoreProductRow,
  type StoreRow,
} from '@/services/competitor';
import { buildStoreColumns, formatMoney } from './storeColumns';

/**
 * 独立站（Shopify）监控。
 *
 * 与 Amazon 卖家清单分成两个 tab 而不是并进一张表：两类对象能拿到的字段几乎不重叠，
 * 合表会让一半的列对另一类恒为空。分组是共用的（同一个 `user_seller_group`），所以
 * 切 tab 不会丢掉分组上下文。
 */
export default function StoreTab({
  groups,
  activeGroupId,
  onGroupsChanged,
}: {
  groups: CompetitorGroup[];
  activeGroupId: number | undefined;
  onGroupsChanged: () => Promise<void> | void;
}) {
  const actionRef = useRef<ActionType>();
  const [addOpen, setAddOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [form] = Form.useForm<{ domain: string; group_id?: number; note?: string }>();

  const [productsOf, setProductsOf] = useState<StoreRow | null>(null);
  const [products, setProducts] = useState<StoreProductRow[]>([]);
  const [productCurrency, setProductCurrency] = useState<string | null>(null);
  const [productsLoading, setProductsLoading] = useState(false);

  const onAdd = async () => {
    const values = await form.validateFields().catch(() => undefined);
    if (!values) return;
    setAdding(true);
    try {
      await addStore({
        domain: values.domain,
        group_id: values.group_id ?? null,
        note: values.note ?? null,
      });
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责。catch 不能省——Button 的
      // onClick 不消费返回的 promise，少了它就是一条 unhandled rejection。
      return;
    } finally {
      setAdding(false);
    }
    message.success('已加入监控，等待首次拉取');
    setAddOpen(false);
    form.resetFields();
    await onGroupsChanged();
    actionRef.current?.reload(true);
  };

  const onChangeGroup = async (domain: string, groupId: number | null) => {
    try {
      await updateStore(domain, groupId === null ? { clear_group: true } : { group_id: groupId });
    } catch {
      return; // Select 的 onChange 同样不消费 promise
    }
    await onGroupsChanged();
    actionRef.current?.reload();
  };

  const onRemove = async (domain: string) => {
    try {
      await removeStore(domain);
    } catch {
      return; // Popconfirm 的 onConfirm 同样不消费 promise
    }
    message.success('已取消关注');
    await onGroupsChanged();
    actionRef.current?.reload();
  };

  const onOpenProducts = async (row: StoreRow) => {
    setProductsOf(row);
    setProductsLoading(true);
    try {
      const res = await listStoreProducts({ domain: row.domain, perPage: 50 });
      setProducts(res.data ?? []);
      setProductCurrency(res.currency ?? null);
    } catch {
      setProducts([]);
    } finally {
      setProductsLoading(false);
    }
  };

  return (
    <>
      <Space style={{ marginBottom: 12 }}>
        <Button type="primary" onClick={() => setAddOpen(true)}>
          添加独立站
        </Button>
      </Space>

      <ProTable<StoreRow>
        actionRef={actionRef}
        rowKey="domain"
        columns={buildStoreColumns(groups, onChangeGroup, onRemove, onOpenProducts)}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{
          emptyText:
            activeGroupId === undefined
              ? '还没有关注任何独立站。点击「添加独立站」粘贴店铺域名'
              : '这个分组里还没有独立站',
        }}
        request={async (params) => {
          const res = await listStores({
            group_id: activeGroupId ?? null,
            page: params.current,
            perPage: params.pageSize,
          });
          return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
        }}
      />

      <Modal
        title="添加独立站"
        open={addOpen}
        confirmLoading={adding}
        onOk={onAdd}
        onCancel={() => setAddOpen(false)}
        okText="加入监控"
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="目前只支持 Shopify 建站的独立站"
          description="粘贴店铺首页地址即可，带不带 https:// 和 www. 都行。加入时会立即校验——不是 Shopify 会当场告诉你，不会等到明天才失败。"
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="domain"
            label="店铺域名"
            rules={[{ required: true, message: '请填写店铺域名' }]}
          >
            <Input placeholder="例如 example.com 或 https://www.example.com/" allowClear />
          </Form.Item>
          <Form.Item name="group_id" label="分组（可选）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="不选则为未分组"
              options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
            />
          </Form.Item>
          <Form.Item name="note" label="备注（可选）">
            <Input maxLength={200} allowClear />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={productsOf ? `${productsOf.name || productsOf.domain} · 在售商品` : ''}
        open={productsOf !== null}
        width={900}
        onClose={() => setProductsOf(null)}
        destroyOnHidden
      >
        <Table<StoreProductRow>
          rowKey="product_id"
          size="small"
          loading={productsLoading}
          dataSource={products}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: '商品', dataIndex: 'title', ellipsis: true },
            {
              title: '价格',
              dataIndex: 'current_price',
              width: 110,
              align: 'right',
              // 币种来自这次响应本身，不去别处查——分开取迟早会拿 A 店的币种显示 B 店的价格
              render: (_, row) => formatMoney(row.current_price, productCurrency),
            },
            {
              title: '店内畅销名次',
              dataIndex: 'latest_rank',
              width: 130,
              align: 'right',
              // 名次是**店内相对名次，不是销量**，也不可跨店比较——数据源不提供绝对销量
              render: (_, row) => (row.latest_rank ? `第 ${row.latest_rank} 名` : '未上榜'),
            },
            {
              title: '上架时间',
              dataIndex: 'published_at',
              width: 170,
              render: (_, row) =>
                row.published_at ? new Date(row.published_at).toLocaleString('zh-CN') : '—',
            },
          ]}
        />
      </Drawer>
    </>
  );
}
