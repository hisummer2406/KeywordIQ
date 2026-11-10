import { ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  message,
} from 'antd';
import { history } from '@umijs/max';
import { useRef, useState } from 'react';
import {
  addStore,
  addStoresBatch,
  MAX_BATCH_DOMAINS,
  type StoreBatchAddResponse,
  listStoreProducts,
  listStores,
  removeStore,
  updateStore,
  type CompetitorGroup,
  type StoreProductRow,
  type StoreRow,
} from '@/services/competitor';
import BatchAddResult from './BatchAddResult';
import { buildStoreColumns, formatMoney } from './storeColumns';

/**
 * Shopify 店铺监控（自动检查）。界面一律称「Shopify 店铺」，不用「独立站」暗示支持任意建站平台（#166 D7）。
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

  const [batchText, setBatchText] = useState('');
  const [batchGroupId, setBatchGroupId] = useState<number | undefined>(undefined);
  const [batchResult, setBatchResult] = useState<StoreBatchAddResponse | null>(null);

  const [productsOf, setProductsOf] = useState<StoreRow | null>(null);
  const [products, setProducts] = useState<StoreProductRow[]>([]);
  const [productCurrency, setProductCurrency] = useState<string | null>(null);
  const [productsLoading, setProductsLoading] = useState(false);

  const closeAddModal = () => {
    setAddOpen(false);
    setBatchResult(null);
  };

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
    closeAddModal();
    form.resetFields();
    await onGroupsChanged();
    actionRef.current?.reload(true);
  };

  const batchDomains = batchText
    .split(/[\n,;\s]+/)
    .map((line) => line.trim())
    .filter(Boolean);

  const onBatchAdd = async () => {
    if (batchDomains.length === 0) {
      message.warning('请先粘贴至少一个域名');
      return;
    }
    if (batchDomains.length > MAX_BATCH_DOMAINS) {
      // 先在前端挡一道给出即时反馈；后端同样会整批拒绝且一条都不写
      message.warning(`一次最多 ${MAX_BATCH_DOMAINS} 个，当前 ${batchDomains.length} 个`);
      return;
    }
    setAdding(true);
    let res;
    try {
      res = await addStoresBatch({ domains: batchDomains, group_id: batchGroupId ?? null });
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责。catch 不能省——Button 的
      // onClick 不消费返回的 promise，少了它就是一条 unhandled rejection。
      return;
    } finally {
      setAdding(false);
    }
    setBatchResult(res);
    // 结果就地展示，弹窗不关——用户要看清哪几条没进去、为什么
    if (res.added.length > 0) {
      setBatchText('');
      await onGroupsChanged();
      actionRef.current?.reload(true);
    }
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
          添加 Shopify 店铺
        </Button>
      </Space>

      <ProTable<StoreRow>
        actionRef={actionRef}
        rowKey="domain"
        columns={buildStoreColumns(groups, onChangeGroup, onRemove, onOpenProducts, (r) =>
          history.push(`/competitor/discovery?domain=${encodeURIComponent(r.domain)}`),
        )}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{
          emptyText:
            activeGroupId === undefined
              ? '还没有关注任何 Shopify 店铺。点击「添加 Shopify 店铺」粘贴店铺域名'
              : '这个分组里还没有 Shopify 店铺',
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
        title="添加 Shopify 店铺"
        open={addOpen}
        confirmLoading={adding}
        onCancel={closeAddModal}
        footer={null}
        width={620}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="只支持 Shopify 建站的店铺，其他建站平台暂不支持"
          description="粘贴店铺地址即可，带不带 https:// 和 www. 都行。加入时会立即校验——不是 Shopify 会当场告诉你，不会等到明天才失败。"
        />
        <Tabs
          defaultActiveKey="single"
          items={[
            {
              key: 'single',
              label: '添加一个',
              children: (
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
                  <Button type="primary" loading={adding} onClick={onAdd}>
                    加入监控
                  </Button>
                </Form>
              ),
            },
            {
              key: 'batch',
              label: '批量粘贴',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input.TextArea
                    rows={8}
                    value={batchText}
                    onChange={(e) => setBatchText(e.target.value)}
                    placeholder={'一行一个，也可以用逗号分隔\nexample.com\nhttps://www.another.com/collections/all'}
                  />
                  <Space>
                    <Select
                      style={{ width: 200 }}
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      placeholder="分组（可选）"
                      value={batchGroupId}
                      onChange={setBatchGroupId}
                      options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
                    />
                    <Button type="primary" loading={adding} onClick={onBatchAdd}>
                      加入这 {batchDomains.length} 个
                    </Button>
                  </Space>
                  {adding && (
                    // 探测每个站点各要发一次请求，几十条会跑十几秒——不说清在做什么，
                    // 用户会以为界面卡住了
                    <Typography.Text type="secondary">
                      正在逐个校验这些站点是不是 Shopify、并读取币种，请稍候……
                    </Typography.Text>
                  )}
                  {batchResult && <BatchAddResult result={batchResult} />}
                </Space>
              ),
            },
          ]}
        />
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
