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

  it('前三卡牌列在，类目列保留', () => {
    const titles = buildColumns('DAY').map((column) => column.title);
    expect(titles).toContain('点击前三 ASIN');
    expect(titles).toContain('类目');
  });

  it('没有「点击前三品牌」列——品牌在报表里是关键词级排行，不是商品属性', () => {
    // 同一 ASIN 在报表中携带互相冲突的品牌值（实测 28.5%），落库时任选其一胜出。
    // 在数据模型修好之前展示它，等于把一个已知错误在每行显示三遍
    const titles = buildColumns('DAY').map((column) => column.title);
    expect(titles).not.toContain('点击前三品牌');
  });
});
