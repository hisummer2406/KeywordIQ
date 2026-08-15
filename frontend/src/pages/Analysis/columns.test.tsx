import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { buildColumns } from './columns';
import { TREND_COLORS } from '@/components/trendColors';

describe('buildColumns', () => {
  it('表头不提供点击排序，统一使用表格工具栏排序', () => {
    expect(buildColumns('DAY').every((column) => column.sorter === undefined)).toBe(true);
    expect(buildColumns('WEEK').every((column) => column.sorter === undefined)).toBe(true);
  });

  it('天视图不混入周排名变化字段', () => {
    expect(buildColumns('DAY').map((column) => column.dataIndex)).not.toContain('week_change');
  });

  it('日视图和周视图都显示同名的搜索趋势列', () => {
    for (const view of ['DAY', 'WEEK'] as const) {
      const trend = buildColumns(view).find((column) => column.dataIndex === 'trend');
      expect(trend?.title).toBe('搜索趋势');
      expect(trend?.hideInTable).not.toBe(true);
    }
  });

  it('不再单列 top1 的商品字段——它们已并入前三卡牌', () => {
    // 同一个数字在一行里出现两次，会让用户误以为是两个不同的量
    const titles = buildColumns('DAY').map((column) => column.title);
    expect(titles).not.toContain('品牌');
    expect(titles).not.toContain('ASIN');
    expect(titles).not.toContain('商品标题');
    expect(titles).not.toContain('点击份额');
    expect(titles).not.toContain('转化份额');
  });

  it('前三卡牌列在，类目列不展示', () => {
    const titles = buildColumns('DAY').map((column) => column.title);
    expect(titles).toContain('点击前三 ASIN');
    // 类目暂不展示：接口仍返回 top1..3_category，需要时把列加回来即可
    expect(titles).not.toContain('类目');
    expect(titles).not.toContain('点击前三类目');
  });

  it('品牌是最后一列', () => {
    const titles = buildColumns('DAY').map((c) => c.title);
    expect(titles).toContain('点击前三品牌');
    // 钉住位置而不只是存在性——列序是刻意的（品牌是回头才看的辅助信息），
    // 只断言"包含"的话，以后有人在末尾加一列就会无声地把它挤走
    expect(titles.at(-1)).toBe('点击前三品牌');
  });

  it('关键词渲染成指向该站点亚马逊的链接，新标签页打开', () => {
    const col = buildColumns('DAY', 'amazon.co.uk').find((c) => c.title === '关键词')!;
    const { container } = render(<>{(col as any).render(null, { keyword: 'power bank' })}</>);
    const a = container.querySelector('a')!;

    expect(a.getAttribute('href')).toBe('https://www.amazon.co.uk/s?k=power%20bank');
    expect(a.getAttribute('target')).toBe('_blank');
    // noopener 不是可选项：没有它，打开的页面能通过 window.opener 操纵本页
    expect(a.getAttribute('rel')).toContain('noopener');
  });

  it('拿不到站点域名时降级为纯文本，不渲染链接', () => {
    const col = buildColumns('DAY', undefined).find((c) => c.title === '关键词')!;
    const { container } = render(<>{(col as any).render(null, { keyword: 'power bank' })}</>);

    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toBe('power bank');
  });
});

// --------------------------------------------------------------------------
// 窗口化行集合：掉榜标签、历史排名、排名变化率
// --------------------------------------------------------------------------

const listedRow = {
  keyword_id: 1, keyword: 'coffee maker', is_dropped: false,
  last_seen_date: null, last_ranking: null,
  current_ranking: 120, previous_ranking: 100, ranking_change: 20, is_new: false,
  history: [100, 90, 80],
} as any;

const droppedRow = {
  keyword_id: 2, keyword: 'gone word', is_dropped: true,
  last_seen_date: '2026-07-17', last_ranking: 305,
  current_ranking: null, previous_ranking: null, ranking_change: null, is_new: false,
  history: [305, null, 210],
} as any;

function cell(view: 'DAY' | 'WEEK', title: string, row: any) {
  const col: any = buildColumns(view).find((c) => c.title === title);
  expect(col, `找不到列: ${title}`).toBeTruthy();
  return render(<>{col.render(null, row)}</>);
}

describe('现排名列吃掉「新词」独立列', () => {
  it('不再有独立的「新词」列', () => {
    expect(buildColumns('DAY').map((c) => c.title)).not.toContain('新词');
  });

  it('新词以标签形式出现在排名列里', () => {
    const { getByText } = cell('DAY', '排名', { ...listedRow, is_new: true });
    expect(getByText('120')).toBeTruthy();
    expect(getByText('新词')).toBeTruthy();
  });

  it('掉榜行显示「已掉榜」与最后在榜期，且不显示当期排名', () => {
    const { getByText, queryByText } = cell('DAY', '排名', droppedRow);
    expect(getByText('已掉榜')).toBeTruthy();
    expect(getByText(/2026-07-17/)).toBeTruthy();
    expect(queryByText('新词')).toBeNull();   // 两标签互斥
  });

  it('掉榜与新词互斥——掉榜优先，不会同时出现', () => {
    const { getByText, queryByText } = cell('DAY', '排名', { ...droppedRow, is_new: true });
    expect(getByText('已掉榜')).toBeTruthy();
    expect(queryByText('新词')).toBeNull();
  });
});

describe('历史排名列', () => {
  it('日视图标签是 昨天 / 3 天前 / 7 天前', () => {
    const { getByText } = cell('DAY', '历史排名', listedRow);
    for (const label of ['昨天', '3 天前', '7 天前']) expect(getByText(label)).toBeTruthy();
  });

  it('周视图标签是 上周 / 4 周前 / 12 周前', () => {
    const { getByText } = cell('WEEK', '历史排名', listedRow);
    for (const label of ['上周', '4 周前', '12 周前']) expect(getByText(label)).toBeTruthy();
  });

  it('三个排名右对齐且用等宽数字，位数差一眼可见', () => {
    // 标签宽度不一（周视图「上周」2 字 vs「12 周前」4 字），数字若紧跟标签就会参差，
    // 而这三行要回答的正是"这个词一路怎么变的"——位数可比是它的全部意义
    const { container } = cell('WEEK', '历史排名', { ...listedRow, history: [305, 12004, 88] });
    const numbers = [...container.querySelectorAll('[data-testid="history-value"]')];

    expect(numbers).toHaveLength(3);
    for (const n of numbers) {
      expect(n).toHaveStyle({ textAlign: 'right', fontVariantNumeric: 'tabular-nums' });
    }
    // 三个数字占同一宽度才谈得上右边缘对齐
    expect(new Set(numbers.map((n) => (n as HTMLElement).style.flex)).size).toBe(1);
  });

  it('参照期缺失显示占位符，且不隐藏该行', () => {
    // 隐藏那一行会让三个参照点错位——用户读到的「4 周前」其实是「12 周前」的值
    const { getByText, getAllByText } = cell('WEEK', '历史排名', droppedRow);
    expect(getByText('上周')).toBeTruthy();
    expect(getByText('4 周前')).toBeTruthy();
    expect(getAllByText('—').length).toBeGreaterThan(0);
  });
});

describe('排名变化率列', () => {
  it('由排名变化与上期排名在展示层算出', () => {
    // 120 名 ← 100 名：变化 +20，相对上期 100 即 20%
    const { getByText } = cell('DAY', '排名变化率', listedRow);
    expect(getByText(/20(\.0)?%/)).toBeTruthy();
  });

  it('符号与「排名变化」列同向：负数=上升', () => {
    const up = { ...listedRow, previous_ranking: 100, ranking_change: -20 };
    const { container } = cell('DAY', '排名变化率', up);
    const changeCell = cell('DAY', '排名变化', up);
    // 两列用同一个渲染约定：上升同色同箭头
    expect(container.textContent).toContain('↑');
    expect(changeCell.container.textContent).toContain('↑');
  });

  it('上期排名为空或为零时显示占位符，不显示 Infinity 或 NaN', () => {
    for (const prev of [null, 0]) {
      const { container } = cell('DAY', '排名变化率', { ...listedRow, previous_ranking: prev });
      expect(container.textContent).toContain('—');
      expect(container.textContent).not.toMatch(/Infinity|NaN/);
    }
  });
});

describe('升降色值来自共享 token', () => {
  // 同一语义在同一屏上用两组色值，会让用户把色差误读成含义差别
  const colored = (title: string, row: any) =>
    cell('DAY', title, row).container.querySelector('span')!;

  it('上升取共享的绿', () => {
    for (const title of ['排名变化', '排名变化率']) {
      expect(colored(title, { ...listedRow, ranking_change: -20 })).toHaveStyle({
        color: TREND_COLORS.up,
      });
    }
  });

  it('下降取共享的红', () => {
    for (const title of ['排名变化', '排名变化率']) {
      expect(colored(title, { ...listedRow, ranking_change: 20 })).toHaveStyle({
        color: TREND_COLORS.down,
      });
    }
  });

  it('无变化是蓝色，不再是灰色', () => {
    for (const title of ['排名变化', '排名变化率']) {
      const span = colored(title, { ...listedRow, ranking_change: 0 });
      expect(span).toHaveStyle({ color: TREND_COLORS.flat });
      expect(span).not.toHaveStyle({ color: '#999999' });
    }
  });
});
