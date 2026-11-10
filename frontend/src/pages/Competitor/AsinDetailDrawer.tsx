import { Drawer, Image, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { getAsinDetail, type AsinDetailResponse } from '@/services/competitor';

/**
 * Amazon ASIN 详情：首次发现时刻（真实数据）+ 卖家级最近采集时刻（近似值）。
 *
 * **两个时刻的可信度不同，界面必须分开说**。首次发现时刻是这个 ASIN 自己的事实
 * （`CompetitorSellerAsin.first_seen_at`）；最近采集时刻是**该卖家**最近一次被采集的
 * 时刻，同一卖家名下其他 ASIN 共享同一个值——当前 schema 没有条目级的"每次出现在
 * 哪些上报里"记录，只能给到这个粒度（issue #65）。**MUST NOT 只给一个裸时间戳**，
 * 那会让用户误读成"这个商品是这时候被采集的"。
 *
 * **只服务 Amazon 行**。独立站走 `ProductDetailDrawer`（issue #65 独立站半，PR #74）
 * ——两类来源能拿到的数据完全不同，合成一个组件只会让大量分支只服务一类来源。
 */

interface Props {
  open: boolean;
  sellerId: string | null;
  site: string | null;
  asin: string | null;
  title: string;
  /** 主图与原商品链接（来自商品发现那一行）；缺失时不显示，不编造。 */
  imageUrl?: string | null;
  productUrl?: string | null;
  onClose: () => void;
}

export default function AsinDetailDrawer({
  open,
  sellerId,
  site,
  asin,
  title,
  imageUrl,
  productUrl,
  onClose,
}: Props) {
  const [data, setData] = useState<AsinDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !sellerId || !site || !asin) return;
    // 同一个竞态问题、同一个修法——见 ProductDetailDrawer.tsx 的注释：关掉一个 ASIN
    // 的抽屉立刻打开另一个，慢响应落地时不能覆盖已经在展示的新抽屉。
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    getAsinDetail(sellerId, site, asin)
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
  }, [open, sellerId, site, asin]);

  return (
    <Drawer title={title} open={open} width={480} onClose={onClose} destroyOnHidden>
      {(imageUrl || asin) && (
        <Space align="start" style={{ marginBottom: 12 }}>
          {imageUrl ? <Image src={imageUrl} width={96} height={96} style={{ objectFit: 'contain' }} /> : null}
          <Space direction="vertical" size={2}>
            {asin ? <Typography.Text type="secondary">ASIN {asin}</Typography.Text> : null}
            {productUrl ? (
              <a href={productUrl} target="_blank" rel="noreferrer">
                打开原商品
              </a>
            ) : null}
          </Space>
        </Space>
      )}
      {loading && <Typography.Text type="secondary">加载中…</Typography.Text>}
      {error && <Typography.Text type="danger">{error}</Typography.Text>}

      {data && (
        <>
          <Typography.Paragraph>
            <Typography.Text strong>首次发现于（不是上架时间）：</Typography.Text>
            {new Date(data.first_seen_at).toLocaleString('zh-CN')}
          </Typography.Paragraph>

          <Typography.Paragraph>
            <Typography.Text strong>该卖家最近一次采集于：</Typography.Text>
            {data.seller_last_reported_at
              ? new Date(data.seller_last_reported_at).toLocaleString('zh-CN')
              : '暂无记录'}
          </Typography.Paragraph>
          {/* 近似值的说明必须常驻显示，不是悬浮提示——用户第一眼就该知道这不是
              这个商品专属的时刻，而不是要主动 hover 才发现。 */}
          <Typography.Text type="secondary">
            这是该卖家整体的最近一次采集时刻，不代表这个商品本身的采集时刻——当前
            数据不记录每个商品各自的每一次出现，同一卖家名下其他商品会显示同一个值。
          </Typography.Text>
        </>
      )}
    </Drawer>
  );
}
