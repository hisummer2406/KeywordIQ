import type { GrowthScopeInfo, GrowthSortField } from '@/services/keyword';

/** 纯函数：口径文案、缺期文案、排序参数转换。无状态、不发请求，直接用 vitest 测。 */

const SORTABLE: GrowthSortField[] = ['rank_gain', 'min_growth_rate', 'end_ranking'];

/**
 * 小数转百分数文案。**换算只在这一处做**——接口、档位、界面各换算一次是份额那条路
 * 踩过的坑：用户拿界面上的数字回填筛选会得到另一个结果。
 */
export function formatPercent(rate: number): string {
  const pct = rate * 100;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(2)}%`;
}

/**
 * 口径说明条的文案。
 *
 * 三个数值**全部来自服务端**（`/scope` 回传），不在这里写死——写死的那一份迟早与后端
 * 实际用的口径分叉，而分叉之后用户读到的说明与他看到的行不是同一个标准。
 */
export function formatCriteria(scope: Pick<
  GrowthScopeInfo,
  'window_days' | 'min_rank_gain' | 'min_daily_rate'
>): string {
  const gain = scope.min_rank_gain.toLocaleString();
  const rate = formatPercent(scope.min_daily_rate);
  return `近 ${scope.window_days} 天名次累计前进超过 ${gain} 名，且每一天的前进幅度都不低于 ${rate}`;
}

/**
 * 缺期提示。窗口内有期次未导入时榜单必然为空——必须说出来，否则「今天没有增长词」
 * 与「有两天数据没导进来」在界面上完全一样。
 *
 * 返回 `null` 表示窗口完整，界面不显示提示条。
 */
export function formatMissingDates(missing: string[]): string | null {
  if (!missing.length) return null;
  return `窗口内缺少 ${missing.join('、')} 共 ${missing.length} 期数据，因此榜单为空——这不是「今天没有增长词」。`;
}

/**
 * ProTable 的 sort 对象 → 接口排序参数。
 *
 * 白名单之外的字段一律忽略并回落默认：后端的 Literal 会把非法字段判 422，而那对用户
 * 表现为「点了下表头，整张表报错」。列上本来就只给这三列挂了 sorter，这里是第二道。
 */
export function toSortParams(sort: Record<string, unknown> | undefined): {
  orderBy: GrowthSortField;
  orderDir: 'asc' | 'desc';
} {
  const entry = Object.entries(sort ?? {}).find(([field, order]) =>
    order && SORTABLE.includes(field as GrowthSortField),
  );
  if (!entry) return { orderBy: 'rank_gain', orderDir: 'desc' };
  return {
    orderBy: entry[0] as GrowthSortField,
    orderDir: entry[1] === 'ascend' ? 'asc' : 'desc',
  };
}
