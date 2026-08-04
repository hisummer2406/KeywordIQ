import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { AnalysisRow } from '@/services/analysis';
import TopProductCards, { topSlots } from './TopProductCards';

/** 一行数据。`slots` 控制有几个槽位有商品。 */
function row(slots: number): AnalysisRow {
  const base: any = {
    keyword_id: 1,
    keyword: 'coffee maker',
    current_ranking: 1,
    previous_ranking: 4,
    ranking_change: -3,
    is_new: false,
  };
  for (let rank = 1; rank <= 3; rank += 1) {
    const filled = rank <= slots;
    base[`top${rank}_asin`] = filled ? `B0SLOT0000${rank}` : null;
    base[`top${rank}_title`] = filled ? `Title ${rank}` : null;
    base[`top${rank}_brand`] = filled ? `BRAND${rank}` : null;
    base[`top${rank}_click_share`] = filled ? 50 / rank : 0;
    base[`top${rank}_conversion_share`] = filled ? 20 / rank : 0;
  }
  return base as AnalysisRow;
}

describe('topSlots', () => {
  it('三个槽位按名次排列，缺失的保留占位而不是被过滤掉', () => {
    const slots = topSlots(row(2));
    expect(slots.map((s) => s.rank)).toEqual([1, 2, 3]);
    expect(slots[2].asin).toBeNull();
  });
});

describe('TopProductCards', () => {
  it('三槽位齐全时渲染三张卡', () => {
    render(<TopProductCards row={row(3)} />);
    expect(screen.getByTestId('slot-card-1')).toBeTruthy();
    expect(screen.getByTestId('slot-card-2')).toBeTruthy();
    expect(screen.getByTestId('slot-card-3')).toBeTruthy();
  });

  it('只有两个商品时第三位留空，前两张不前移', () => {
    // 位置与名次的对应关系是这批字段的全部意义——收拢就读不出名次了
    render(<TopProductCards row={row(2)} />);
    expect(screen.getByTestId('slot-card-1')).toBeTruthy();
    expect(screen.getByTestId('slot-card-2')).toBeTruthy();
    expect(screen.queryByTestId('slot-card-3')).toBeNull();
    expect(screen.getByTestId('slot-empty-3')).toBeTruthy();
  });

  it('份额按百分数原样呈现，不做换算', () => {
    render(<TopProductCards row={row(1)} />);
    expect(screen.getByText('点击 50%')).toBeTruthy();
    expect(screen.getByText('转化 20%')).toBeTruthy();
  });

  it('图片加载失败降级为占位方块，ASIN 与份额照常展示', () => {
    render(<TopProductCards row={row(1)} />);
    fireEvent.error(screen.getByAltText('B0SLOT00001'));

    expect(screen.getByTestId('thumb-placeholder')).toBeTruthy();
    expect(screen.getByText('B0SLOT00001')).toBeTruthy();
    expect(screen.getByText('点击 50%')).toBeTruthy();
  });

  it('图床返回 1×1 透明像素时也降级——它是 200，onError 不会触发', () => {
    // 实测：不存在的 ASIN 返回 HTTP 200 + 43 字节的 1×1 透明 GIF。只挂 onError 的话，
    // 用户看到的是一个被拉伸的空白方块，比占位符更糟——它看起来像"图还在加载"
    render(<TopProductCards row={row(1)} />);
    const img = screen.getByAltText('B0SLOT00001') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 1, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 1, configurable: true });
    fireEvent.load(img);

    expect(screen.getByTestId('thumb-placeholder')).toBeTruthy();
    expect(screen.getByText('点击 50%')).toBeTruthy();
  });

  it('正常尺寸的图片不被误判为占位像素', () => {
    render(<TopProductCards row={row(1)} />);
    const img = screen.getByAltText('B0SLOT00001') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 112, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 112, configurable: true });
    fireEvent.load(img);

    expect(screen.queryByTestId('thumb-placeholder')).toBeNull();
  });
});
