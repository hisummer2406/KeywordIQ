import { useState } from 'react';
import { Tooltip, Typography } from 'antd';
import type { AnalysisRow } from '@/services/analysis';
import { amazonProductUrl } from './amazonProductUrl';
import type { HistoryScope } from './columns';
import { PLACEHOLDER_PIXEL_SIZE, productImageUrl } from './productImage';
import TopslotHistoryPopover from './TopslotHistoryPopover';

/**
 * 点击量前三商品的卡牌列与品牌列。
 *
 * 三个位置**固定对应**第一、第二、第三名：某个槽位没有商品时该位置留空，不由后一名
 * 顶替、剩余卡牌也不收拢——横向对齐是用户跨行比较名次的唯一依据。
 *
 * 份额沿用 0~100 的百分数口径，与过滤区条件、导出内容三处一致，任何一处都不做换算。
 */

/**
 * 一个商品槽位。
 *
 * **刻意不含 brand。** `top{N}_brand` 是「搜这个词的人点击集中在哪几个品牌」这份
 * 关键词级排行的第 N 名，与「点击量第 N 的商品是谁」是两份彼此独立的榜单。把它放进
 * 槽位就等于重新宣称「第 N 个商品的品牌是它」——那正是 keyword-level-brand-category
 * 拆掉的错误配对（旧模型把品牌#N 与商品#N 硬配写进 dim_product，实测 11.7% 的 ASIN
 * 因此携带互相冲突的品牌值）。品牌按名次单独成列，见 columns.tsx 的 RankedList。
 */
export interface TopSlot {
  rank: number;
  asin: string | null;
  title: string | null;
  clickShare: number;
  conversionShare: number;
}

/** 把一行的三组扁平字段拼成按名次排列的槽位数组。空槽位保留占位，不过滤。 */
export function topSlots(row: AnalysisRow): TopSlot[] {
  return [1, 2, 3].map((rank) => ({
    rank,
    asin: row[`top${rank}_asin` as keyof AnalysisRow] as string | null,
    title: row[`top${rank}_title` as keyof AnalysisRow] as string | null,
    clickShare: (row[`top${rank}_click_share` as keyof AnalysisRow] as number) ?? 0,
    conversionShare: (row[`top${rank}_conversion_share` as keyof AnalysisRow] as number) ?? 0,
  }));
}

const CARD_WIDTH = 112;
const THUMB_SIZE = 72;

/**
 * 缩略图：取不到图时降级为 ASIN 首字母占位方块，不影响卡牌其余信息。
 *
 * **不能只靠 `onError`**：图床对不存在的 ASIN 返回的是 HTTP 200 + 1×1 透明 GIF，
 * 而不是 404，`onError` 因此永远不会触发，结果是一个被拉伸的空白方块——比占位符更糟，
 * 因为它看起来像"图还在加载"。所以加载完成后要检查真实尺寸，1×1 即判定为无图。
 * `onError` 仍保留，它负责真正的网络失败。
 *
 * `failed` 是组件内部状态，**调用方必须按 asin 给 key**（见 SlotCard）：表格的 rowKey
 * 是 keyword_id，切换日期时同一个关键词的行不会重建，但它的 top1 商品是会变的。
 * 不给 key 的话，上一个日期没图的行会把"没图"这个结论带给下一个日期的另一个商品。
 */
function Thumbnail({ asin }: { asin: string }) {
  const [failed, setFailed] = useState(false);
  const url = productImageUrl(asin);

  if (!url || failed) {
    return (
      <div
        data-testid="thumb-placeholder"
        style={{
          width: THUMB_SIZE,
          height: THUMB_SIZE,
          borderRadius: 4,
          background: '#f5f5f5',
          border: '1px solid #f0f0f0',
          // inline-flex 而非 flex：块级元素不会被卡牌的 text-align: center 居中，
          // 而 <img> 是 inline-block 会居中——两者不一致会让"有图"和"没图"的卡牌歪成两样
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#bfbfbf',
          fontSize: 18,
        }}
      >
        {asin.slice(0, 1)}
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={asin}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      onLoad={(e) => {
        const img = e.currentTarget;
        if (img.naturalWidth <= PLACEHOLDER_PIXEL_SIZE || img.naturalHeight <= PLACEHOLDER_PIXEL_SIZE) {
          setFailed(true);
        }
      }}
      style={{
        width: THUMB_SIZE,
        height: THUMB_SIZE,
        objectFit: 'contain',
        borderRadius: 4,
        border: '1px solid #f0f0f0',
        background: '#fff',
      }}
    />
  );
}

function SlotCard({
  slot, siteDomain, keywordId, historyScope,
}: {
  slot: TopSlot;
  siteDomain?: string;
  keywordId: number;
  historyScope?: HistoryScope;
}) {
  // 空槽位渲染等宽占位：位置必须留着，否则名次与位置的对应关系就断了
  if (!slot.asin) {
    return (
      <div
        data-testid={`slot-empty-${slot.rank}`}
        style={{ width: CARD_WIDTH, textAlign: 'center', color: '#d9d9d9', fontSize: 12 }}
      >
        —
      </div>
    );
  }

  const href = amazonProductUrl(slot.asin, siteDomain);

  const card = (
    <div data-testid={`slot-card-${slot.rank}`} style={{ width: CARD_WIDTH, textAlign: 'center' }}>
      {/* key 按 asin：商品换了就重建，不把上一个商品的"没图"结论继承下来 */}
      <Thumbnail key={slot.asin} asin={slot.asin} />
      <Typography.Text
        copyable={{ text: slot.asin }}
        style={{ display: 'block', fontSize: 11, marginTop: 2 }}
      >
        {slot.asin}
      </Typography.Text>
      <div style={{ fontSize: 11, color: '#1677ff', fontVariantNumeric: 'tabular-nums' }}>
        点击 {slot.clickShare}%
      </div>
      <div style={{ fontSize: 11, color: '#52c41a', fontVariantNumeric: 'tabular-nums' }}>
        转化 {slot.conversionShare}%
      </div>
    </div>
  );

  // 链接包在**整张卡牌**外而不是图片上：图床覆盖率很低（实测 12 个 ASIN 仅 2 个有图），
  // 占位方块是常态，而那恰恰是用户最需要"去看看这到底是什么商品"的时候。
  // 拿不到域名就不给链接——宁可不可点，也不要拼出一个会跳错的地址。
  const linked = href ? (
    <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit' }}>
      {card}
    </a>
  ) : (
    card
  );

  // 悬浮历史面板（issue #96）取代普通 Tooltip 承担"展示完整商品标题"这件事——面板
  // 标题栏就是这个身份的展示位。历史作用域拿不到（还没查出首屏数据）时退化回原来的
  // 纯 Tooltip，不给一个必定失败的入口。Popover 由 rc-trigger 挂载到独立浮层容器，
  // 与这里的 <a> 不共享 DOM 树，面板内的滚动/重试点击不会冒泡触发外链跳转。
  if (!historyScope) {
    return (
      <Tooltip title={slot.title || slot.asin} placement="top">
        {linked}
      </Tooltip>
    );
  }
  return (
    <TopslotHistoryPopover
      site={historyScope.site}
      view={historyScope.view}
      on={historyScope.on}
      keywordId={keywordId}
      kind="ASIN"
      value={slot.asin}
      label={slot.title || slot.asin}
    >
      {linked}
    </TopslotHistoryPopover>
  );
}

/** 三张卡牌横排，从左到右是点击量第一、第二、第三名。 */
export default function TopProductCards({
  row,
  siteDomain,
  historyScope,
}: {
  row: AnalysisRow;
  siteDomain?: string;
  historyScope?: HistoryScope;
}) {
  return (
    <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
      {topSlots(row).map((slot) => (
        <SlotCard
          key={slot.rank}
          slot={slot}
          siteDomain={siteDomain}
          keywordId={row.keyword_id}
          historyScope={historyScope}
        />
      ))}
    </div>
  );
}
