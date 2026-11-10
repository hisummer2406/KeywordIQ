import { Tag, Tooltip } from 'antd';
import type { StoreRow, StoreStatus } from '@/services/competitor';

/**
 * 独立站的状态呈现。
 *
 * **四种状态绝不合并成一种**（spec「三类失败状态分开呈现」+「从未拉取」）。每一种的
 * 用户动作都不同，混成一句「抓取失败」会让人做错事：
 *
 * | 状态 | 真相 | 用户该做什么 |
 * |---|---|---|
 * | 从未拉取 | 刚加入，还没到点 | 等一会儿，**不是坏了** |
 * | 未开放商品接口 | 商户自己关掉的 | 没法监控，考虑移除 |
 * | 抓取失败 | 故障 | 看连续失败次数，多了就报修 |
 * | 数据不完整 | 拉了但没拉全 | 本次不判下架，下次会补 |
 *
 * 尤其是「未开放商品接口」被报成「抓取失败」时，用户会反复等、反复重试，而那个端点
 * 永久没有了。
 */

const STATUS_META: Record<StoreStatus, { color: string; text: string; hint: string }> = {
  NEVER_FETCHED: {
    color: 'default',
    text: '待首次拉取',
    hint: '刚加入，还没到拉取时间。不是故障，等下一轮即可。',
  },
  OK: { color: 'green', text: '自动检查正常', hint: '后台定时自动检查，上次拉取完整成功。' },
  INCOMPLETE: {
    color: 'gold',
    text: '数据不完整',
    hint: '上次没能取到全部商品，因此本次不做下架判定，下一轮会补齐。',
  },
  FAILED: {
    color: 'red',
    text: '抓取失败',
    hint: '上次拉取失败，可能是网络或站点临时问题。',
  },
  NO_CATALOG_API: {
    color: 'volcano',
    text: '未开放商品接口',
    hint: '这家店关闭了公开的商品接口，我们拿不到它的商品清单。这是商户自己的设置，不是故障，重试也不会好。',
  },
};

export function renderStoreStatus(row: StoreRow) {
  const meta = STATUS_META[row.status] ?? STATUS_META.FAILED;
  const suffix =
    row.status === 'FAILED' && row.consecutive_failures > 1
      ? `（连续 ${row.consecutive_failures} 次）`
      : '';
  return (
    <Tooltip title={meta.hint}>
      <Tag color={meta.color}>
        {meta.text}
        {suffix}
      </Tag>
    </Tooltip>
  );
}

/**
 * 畅销榜解析异常的独立提示。
 *
 * **绝不能显示成「该站销量榜为空」**：目录里有商品、畅销榜却一个都提取不出来，只可能
 * 是页面结构变了。当成空榜单的表现是「这家店的销量榜突然空了」，而没有任何地方会报错。
 * 它与目录状态相互独立——名次挂了不影响新品与价格。
 */
export function renderRankHealth(row: StoreRow) {
  if (row.status === 'NEVER_FETCHED') return null;
  if (row.rank_ok) return null;
  return (
    <Tooltip title="畅销榜页面取到了，但一个商品都没解析出来——多半是页面结构变了，不是这家店没有销量。新品与价格不受影响。">
      <Tag color="orange">畅销榜解析异常</Tag>
    </Tooltip>
  );
}
