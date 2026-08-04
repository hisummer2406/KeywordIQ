import { useState } from 'react';
import { Tooltip, Typography } from 'antd';
import type { AnalysisRow } from '@/services/analysis';
import { PLACEHOLDER_PIXEL_SIZE, productImageUrl } from './productImage';

/**
 * 点击量前三商品的卡牌列与品牌列。
 *
 * 三个位置**固定对应**第一、第二、第三名：某个槽位没有商品时该位置留空，不由后一名
 * 顶替、剩余卡牌也不收拢——横向对齐是用户跨行比较名次的唯一依据。
 *
 * 份额沿用 0~100 的百分数口径，与过滤区条件、导出内容三处一致，任何一处都不做换算。
 */

export interface TopSlot {
  rank: number;
  asin: string | null;
  title: string | null;
  brand: string | null;
  clickShare: number;
  conversionShare: number;
}

/** 把一行的三组扁平字段拼成按名次排列的槽位数组。空槽位保留占位，不过滤。 */
export function topSlots(row: AnalysisRow): TopSlot[] {
  return [1, 2, 3].map((rank) => ({
    rank,
    asin: row[`top${rank}_asin` as keyof AnalysisRow] as string | null,
    title: row[`top${rank}_title` as keyof AnalysisRow] as string | null,
    brand: row[`top${rank}_brand` as keyof AnalysisRow] as string | null,
    clickShare: (row[`top${rank}_click_share` as keyof AnalysisRow] as number) ?? 0,
    conversionShare: (row[`top${rank}_conversion_share` as keyof AnalysisRow] as number) ?? 0,
  }));
}

const CARD_WIDTH = 92;
const THUMB_SIZE = 56;

/**
 * 缩略图：取不到图时降级为 ASIN 首字母占位方块，不影响卡牌其余信息。
 *
 * **不能只靠 `onError`**：图床对不存在的 ASIN 返回的是 HTTP 200 + 1×1 透明 GIF，
 * 而不是 404，`onError` 因此永远不会触发，结果是一个被拉伸的空白方块——比占位符更糟，
 * 因为它看起来像"图还在加载"。所以加载完成后要检查真实尺寸，1×1 即判定为无图。
 * `onError` 仍保留，它负责真正的网络失败。
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
          display: 'flex',
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

function SlotCard({ slot }: { slot: TopSlot }) {
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

  return (
    <Tooltip title={slot.title || slot.asin} placement="top">
      <div data-testid={`slot-card-${slot.rank}`} style={{ width: CARD_WIDTH, textAlign: 'center' }}>
        <Thumbnail asin={slot.asin} />
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
    </Tooltip>
  );
}

/** 三张卡牌横排，从左到右是点击量第一、第二、第三名。 */
export default function TopProductCards({ row }: { row: AnalysisRow }) {
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {topSlots(row).map((slot) => (
        <SlotCard key={slot.rank} slot={slot} />
      ))}
    </div>
  );
}
