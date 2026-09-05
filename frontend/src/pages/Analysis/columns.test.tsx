import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { buildColumns, totalColumnWidth } from './columns';
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

  it('前三卡牌列与类目列都在，且类目排在品牌之前', () => {
    const titles = buildColumns('DAY').map((column) => column.title);
    expect(titles).toContain('点击前三 ASIN');
    expect(titles).toContain('点击前三类目');
    // 类目是"预言中的那个新列"——它必须插在品牌之前，否则会把恒为末列的品牌挤走
    expect(titles.indexOf('点击前三类目')).toBeLessThan(titles.indexOf('点击前三品牌'));
  });

  it('操作是最后一列，品牌退居倒数第二（issue #78 引入操作列后的顺序）', () => {
    const titles = buildColumns('DAY').map((c) => c.title);
    expect(titles).toContain('点击前三品牌');
    expect(titles).toContain('操作');
    // 钉住位置而不只是存在性——列序是刻意的，只断言"包含"的话，
    // 以后有人在末尾加一列就会无声地把它挤走
    expect(titles.at(-1)).toBe('操作');
    expect(titles.at(-2)).toBe('点击前三品牌');
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

  it('操作列渲染 Google 搜索趋势 + Meta 广告库两条直达链接，新标签页打开（issue #78 V0，不做抽屉）', () => {
    const col = buildColumns('DAY', 'amazon.com', 'US').find((c) => c.title === '操作')!;
    const { container } = render(<>{(col as any).render(null, { keyword: 'power bank' })}</>);
    const links = [...container.querySelectorAll('a')];
    expect(links).toHaveLength(2);

    const trends = links.find((a) => a.getAttribute('href')?.includes('trends.google.com'))!;
    expect(trends.getAttribute('href')).toBe(
      'https://trends.google.com/trends/explore?q=power+bank&geo=US&date=today+12-m',
    );
    const adLibrary = links.find((a) => a.getAttribute('href')?.includes('facebook.com'))!;
    expect(adLibrary.getAttribute('href')).toBe(
      'https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=power+bank&search_type=keyword_unordered',
    );
    for (const a of links) {
      expect(a.getAttribute('target')).toBe('_blank');
      expect(a.getAttribute('rel')).toContain('noopener');
    }
    // 不弹抽屉、不带按钮/弹层——点击即直接跳转
    expect(container.querySelector('button')).toBeNull();
  });

  it('操作列拿不到站点码时两条入口都不渲染', () => {
    const col = buildColumns('DAY', 'amazon.com').find((c) => c.title === '操作')!;
    const { container } = render(<>{(col as any).render(null, { keyword: 'power bank' })}</>);
    expect(container.querySelector('a')).toBeNull();
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

  it('标签右对齐、数字左对齐——两边朝中间靠，不留大空隙', () => {
    // 两次踩坑的回归：数字 flex:1 撑满会顶到列最右端；两段定宽但都朝外对齐会把空隙加两遍
    const { container } = cell('WEEK', '历史排名', { ...listedRow, history: [305, 12004, 88] });
    const numbers = [...container.querySelectorAll('[data-testid="history-value"]')];

    expect(numbers).toHaveLength(3);
    for (const n of numbers) {
      const style = (n as HTMLElement).style;
      expect(style.textAlign).toBe('left');
      expect(style.fontVariantNumeric).toBe('tabular-nums');
      expect(style.flex).toBe('');   // 不撑满
      expect(style.width).toBe('');  // 不定宽——定宽会再次制造空隙
    }
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

describe('横向空间分配', () => {
  // 内容宽度固定的列必须定宽；靠"不设宽度"吸收富余，只会在它与右邻列之间留下大片空白
  it('商品卡牌列定宽（内容是固定的三张等宽卡牌）', () => {
    const col = buildColumns('DAY').find((c) => c.title === '点击前三 ASIN')!;
    expect(col.width).toBeGreaterThanOrEqual(292); // 3×92 + 2×8
  });

  it('每一列都定宽——只要有一列不定宽，它就会独吞富余并造出一个洞', () => {
    // 试过三种分法（卡牌列独吞 / 关键词独吞 / 类目品牌平分），洞只是换位置，没有消失。
    // 全部定宽后，多余宽度由浏览器按比例摊给每一列，不堆在任何一处。
    for (const view of ['DAY', 'WEEK'] as const) {
      for (const col of buildColumns(view)) {
        expect(col.width, `列「${String(col.title)}」没有定宽，它会独吞横向富余`).toBeDefined();
      }
    }
  });

  it('关键词超长时换行而不是省略号——被藏掉的后半截往往正是区分同类词的部分', () => {
    const col = buildColumns('DAY').find((c) => c.title === '关键词')!;
    expect(col.ellipsis).toBeFalsy();
  });
});

describe('历史排名的标签朝数字靠', () => {
  it('标签右对齐，使其紧邻数字而不是贴在列的左边', () => {
    const { container } = cell('DAY', '历史排名', listedRow);
    const labels = [...container.querySelectorAll('span')].filter((s) =>
      ['昨天', '3 天前', '7 天前'].includes(s.textContent || ''),
    );

    expect(labels.length).toBeGreaterThan(0);
    for (const l of labels) expect((l as HTMLElement).style.textAlign).toBe('right');
  });
});

describe('表头一律居中；内容按列宽是否随行变化决定对齐', () => {
  it('表头一律居中', () => {
    for (const view of ['DAY', 'WEEK'] as const) {
      for (const col of buildColumns(view)) {
        expect((col.onHeaderCell as any)?.().style.textAlign).toBe('center');
      }
    }
  });

  it('内容宽度随行变化的列必须左对齐——居中会让每行起点不同，往下扫时左右横跳', () => {
    const cols = buildColumns('DAY');
    for (const title of ['关键词', '历史排名', '点击前三类目', '点击前三品牌']) {
      expect(cols.find((c) => c.title === title)!.align, `「${title}」居中会横跳`).toBe('left');
    }
  });

  it('内容宽度固定的列居中', () => {
    const cols = buildColumns('DAY');
    for (const title of ['排名', '搜索趋势', '排名变化', '排名变化率', '点击前三 ASIN', '操作']) {
      expect(cols.find((c) => c.title === title)!.align).toBe('center');
    }
  });
});

describe('列宽必须真正生效', () => {
  it('总宽等于各列之和——表格据此切到 table-layout: fixed', () => {
    // 不给 scroll.x 时 AntD 用 table-layout: auto，列上的 width 只是建议值，浏览器按内容
    // 抢宽度：同样写 130px 的类目与品牌会渲染成两个宽度。这条钉住总宽由列定义算出，
    // 写死的话加列或改宽后会与实际对不上，差额又被分摊回各列，等于退回 auto 的老问题。
    for (const view of ['DAY', 'WEEK'] as const) {
      const sum = buildColumns(view).reduce((s, c) => s + Number(c.width), 0);
      expect(totalColumnWidth(view)).toBe(sum);
      expect(sum).toBeGreaterThan(0);
    }
  });

  it('类目与品牌同宽——它们是并列的辅助信息，宽度不同会显得一个比另一个重要', () => {
    const cols = buildColumns('DAY');
    const category = cols.find((c) => c.title === '点击前三类目')!.width;
    expect(category).toBe(cols.find((c) => c.title === '点击前三品牌')!.width);
  });
});
