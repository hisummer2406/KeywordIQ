import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { buildColumns } from './columns';

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
