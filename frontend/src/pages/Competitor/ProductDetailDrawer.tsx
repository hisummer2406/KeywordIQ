import { Drawer, Table, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import {
  getProductHistory,
  type PricePoint,
  type ProductHistoryResponse,
  type RankPoint,
} from '@/services/competitor';
import { formatMoney } from './storeColumns';

/**
 * 独立站商品详情：变体价格历史 + 店内名次历史。
 *
 * **展示形式是表格，不是图表**。价格观测点"仅在取值变化时追加"（`competitor_store_price`
 * 的既有约束），在时间轴上不均匀分布——可能几周只有一个点，也可能一天调价两次。套用
 * 现有的 `TrendSparkline`（为等距点设计：日视图 7 点、周视图 4 点）会把不均匀的间隔
 * 压缩成等距点，误导"调价频率"这件事本身。表格按时间倒序陈列，不需要为此新建一个
 * 变长时间轴的图表组件。
 *
 * **只服务独立站（DTC）行**。Amazon 侧的详情仍是已知缺口（issue #65），点击 Amazon 行
 * 不会打开本组件——调用方（`NewArrivalsTab`）负责这道判断，本组件不重复检查来源。
 */

interface Props {
  open: boolean;
  domain: string | null;
  productId: number | null;
  title: string;
  onClose: () => void;
}

export default function ProductDetailDrawer({ open, domain, productId, title, onClose }: Props) {
  const [data, setData] = useState<ProductHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !domain || productId === null) return;
    // 关掉商品 A 的抽屉立刻打开商品 B：A 的慢响应此时仍在飞，若不设防它落地时会
    // 用 A 的数据覆盖已经在展示 B 的抽屉——界面上是"B 的标题 + A 的历史"，或者
    // B 明明还在等却被 A 提前结束了 loading。`cancelled` 只在这次 effect 的
    // cleanup（下一次 open/domain/productId 变化，或组件卸载）时置位，三个
    // 回调落地前都要重新检查。
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    getProductHistory(domain, productId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, domain, productId]);

  const currency = data?.currency ?? null;

  return (
    <Drawer title={title} open={open} size={720} onClose={onClose} destroyOnHidden>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}

      <Typography.Title level={5}>价格历史</Typography.Title>
      <Table<PricePoint>
        rowKey={(row) => `${row.variant_id}-${row.observed_at}`}
        size="small"
        loading={loading}
        dataSource={data?.prices ?? []}
        pagination={{ pageSize: 10 }}
        // 能打开这个抽屉的行本身就来自"有历史"的数据（上新流只收纳已采集到的商品），
        // 空历史理论上不会发生；空态文案仍然给一句明确的话，而不是裸表格。
        locale={{ emptyText: '暂无价格记录' }}
        columns={[
          {
            title: '时间',
            dataIndex: 'observed_at',
            width: 170,
            render: (_, row) => new Date(row.observed_at).toLocaleString('zh-CN'),
          },
          {
            title: '变体',
            dataIndex: 'sku',
            width: 140,
            ellipsis: true,
            // sku 不是每个变体都有，缺失时退回 variant_id——不显示空白，那看着像加载出错
            render: (_, row) => row.sku || `#${row.variant_id}`,
          },
          {
            title: '价格',
            dataIndex: 'price',
            width: 110,
            align: 'right',
            render: (_, row) => formatMoney(row.price, currency),
          },
          {
            title: '划线价',
            dataIndex: 'compare_at_price',
            width: 110,
            align: 'right',
            // 没有划线价就是没有，不显示 0——0 会被读成"免费"
            render: (_, row) => (row.compare_at_price ? formatMoney(row.compare_at_price, currency) : '—'),
          },
          {
            title: '可购买',
            dataIndex: 'available',
            width: 90,
            align: 'center',
            // 变体的 available 只表示「此刻能不能买」，**不是下架**——下架是商品从目录里消失，
            // 由目录事件表示。写成「下架」会让一次售罄被读成商品没了。
            render: (_, row) =>
              row.available ? <Tag color="green">可购买</Tag> : <Tag>不可购买</Tag>,
          },
        ]}
      />

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        店内名次历史
      </Typography.Title>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
        店内相对名次，不是销量，不可跨店比较——数据源不提供任何绝对销量。
      </Typography.Text>
      <Table<RankPoint>
        rowKey="captured_on"
        size="small"
        loading={loading}
        dataSource={data?.ranks ?? []}
        pagination={{ pageSize: 10 }}
        locale={{ emptyText: '暂无名次记录' }}
        columns={[
          { title: '日期', dataIndex: 'captured_on', width: 170 },
          {
            title: '店内名次',
            dataIndex: 'rank',
            align: 'right',
            render: (_, row) => `第 ${row.rank} 名`,
          },
        ]}
      />
    </Drawer>
  );
}
