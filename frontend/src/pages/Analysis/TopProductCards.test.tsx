import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnalysisRow } from '@/services/analysis';

// TopslotHistoryPopover 自身的悬浮/分页/竞态行为在它自己的测试文件里覆盖（issue #96）；
// 这里只关心"历史作用域到位时是否把正确的身份参数接进了它"，用一个透传 props 的桩替身
// 避免重复驱动真实的 hover 计时与网络请求。
vi.mock('./TopslotHistoryPopover', () => ({
  default: (props: any) => (
    <div data-testid="topslot-history-popover" data-props={JSON.stringify({
      site: props.site, view: props.view, keywordId: props.keywordId,
      on: props.on, kind: props.kind, value: props.value, label: props.label,
    })}>
      {props.children}
    </div>
  ),
}));

import TopProductCards, { topSlots } from './TopProductCards';

/** 一行数据。`slots` 控制有几个槽位有商品。 */
function row(slots: number): AnalysisRow {
  const base: any = {
    keyword_id: 1,
    keyword: 'coffee maker',
    current_ranking: 1,
    previous_ranking: 4,
    ranking_change: -3,
    first_listed: false,
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

  it('图片加载失败时不显示图片也不显示占位，ASIN 与份额照常展示', () => {
    render(<TopProductCards row={row(1)} />);
    fireEvent.error(screen.getByAltText('B0SLOT00001'));

    expect(screen.queryByAltText('B0SLOT00001')).toBeNull();
    expect(screen.queryByTestId('thumb-placeholder')).toBeNull();
    expect(screen.getByText('B0SLOT00001')).toBeTruthy();
    expect(screen.getByText('点击 50%')).toBeTruthy();
  });

  it('图床返回 1×1 透明像素时同样移除图片——它是 200，onError 不会触发', () => {
    // 实测：不存在的 ASIN 返回 HTTP 200 + 43 字节的 1×1 透明 GIF。只挂 onError 的话，
    // 用户看到的是一个被拉伸的空白方块，比占位符更糟——它看起来像"图还在加载"
    render(<TopProductCards row={row(1)} />);
    const img = screen.getByAltText('B0SLOT00001') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 1, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 1, configurable: true });
    fireEvent.load(img);

    expect(screen.queryByAltText('B0SLOT00001')).toBeNull();
    expect(screen.getByText('点击 50%')).toBeTruthy();
  });

  it('正常尺寸的图片不被误判为占位像素', () => {
    render(<TopProductCards row={row(1)} />);
    const img = screen.getByAltText('B0SLOT00001') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 112, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 112, configurable: true });
    fireEvent.load(img);

    expect(screen.getByAltText('B0SLOT00001')).toBeTruthy();
  });
});

describe('卡牌可点击至商品页', () => {
  it('卡牌整体是链接，新标签页打开该站点商品详情页', () => {
    render(<TopProductCards row={row(3)} siteDomain="amazon.com" />);

    const link = screen.getByTestId('slot-card-1').closest('a')!;
    expect(link.getAttribute('href')).toBe('https://www.amazon.com/dp/B0SLOT00001');
    expect(link.getAttribute('target')).toBe('_blank');
    // 没有 noopener，打开的页面能通过 window.opener 操纵本页
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('取不到图的卡牌同样可点击——那恰恰是最需要这个入口的时候', () => {
    // 图床对本项目 ASIN 覆盖率很低（实测 12 个中仅 2 个有图），无图卡牌是常态而非例外。
    // 链接挂在卡牌而非图片上，故有无图都可点。
    render(<TopProductCards row={row(3)} siteDomain="amazon.com" />);
    const card = screen.getByTestId('slot-card-2');

    expect(card.closest('a')).not.toBeNull();
  });

  it('域名随站点变化', () => {
    render(<TopProductCards row={row(1)} siteDomain="amazon.co.uk" />);

    expect(screen.getByTestId('slot-card-1').closest('a')!.getAttribute('href')).toBe(
      'https://www.amazon.co.uk/dp/B0SLOT00001',
    );
  });

  it('拿不到域名时降级为不可点击，不拼出错误地址', () => {
    render(<TopProductCards row={row(1)} />);

    expect(screen.getByTestId('slot-card-1').closest('a')).toBeNull();
  });
});

describe('悬浮历史入口（issue #96）', () => {
  const historyScope = { site: 'US', view: 'DAY' as const, on: '2026-07-10' };

  it('没有历史作用域时不接入悬浮历史（还没查出首屏数据）', () => {
    render(<TopProductCards row={row(1)} />);
    expect(screen.queryByTestId('topslot-history-popover')).toBeNull();
  });

  it('历史作用域到位时，每个非空槽位都接入正确的身份参数', () => {
    render(<TopProductCards row={row(2)} historyScope={historyScope} />);
    const popovers = screen.getAllByTestId('topslot-history-popover');
    expect(popovers).toHaveLength(2); // 只有两个槽位有商品

    const first = JSON.parse(popovers[0].dataset.props!);
    expect(first).toEqual({
      site: 'US', view: 'DAY', keywordId: 1, on: '2026-07-10',
      kind: 'ASIN', value: 'B0SLOT00001', label: 'Title 1',
    });
  });

  it('空槽位不接入悬浮历史', () => {
    render(<TopProductCards row={row(1)} historyScope={historyScope} />);
    expect(screen.getAllByTestId('topslot-history-popover')).toHaveLength(1);
  });
});
