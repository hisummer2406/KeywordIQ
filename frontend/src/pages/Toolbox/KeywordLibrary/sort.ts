import type { LibrarySortField } from '@/services/library';

/** 纯函数：排序参数转换。无状态、不发请求，直接用 vitest 测。 */

const SORTABLE: LibrarySortField[] = [
  'current_ranking',
  'ranking_change',
  'previous_ranking',
  'top1_click_share',
  'top1_conversion_share',
  'added_at',
];

/**
 * ProTable 的 sort 对象 → 接口排序参数。
 *
 * 白名单之外的字段一律忽略并回落默认（排名升序）：后端的 Literal 会把非法字段判 400，
 * 而那对用户表现为「点了下表头，整张表报错」。
 *
 * 走势与历史排名不在白名单里——它们是数组，排序对它们没有定义。
 */
export function toSortParams(sort: Record<string, unknown> | undefined): {
  orderBy: LibrarySortField;
  orderDir: 'asc' | 'desc';
} {
  const entry = Object.entries(sort ?? {}).find(
    ([field, order]) => order && SORTABLE.includes(field as LibrarySortField),
  );
  if (!entry) return { orderBy: 'current_ranking', orderDir: 'asc' };
  return {
    orderBy: entry[0] as LibrarySortField,
    orderDir: entry[1] === 'ascend' ? 'asc' : 'desc',
  };
}
