import { describe, expect, it } from 'vitest';
import { buildColumns } from './columns';

describe('buildColumns', () => {
  it('表头不提供点击排序，统一使用表格工具栏排序', () => {
    expect(buildColumns('DAY').every((column) => column.sorter === undefined)).toBe(true);
    expect(buildColumns('WEEK').every((column) => column.sorter === undefined)).toBe(true);
  });
});
