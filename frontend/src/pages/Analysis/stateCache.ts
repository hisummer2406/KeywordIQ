import type { AnalysisRow, CategoryOption, SearchBody, View } from '@/services/analysis';
import type { SiteOption } from '@/services/upload';
import type { FilterDraft, ScopeDraft } from './filters';

/**
 * 分析查询屏的会话内状态缓存。
 *
 * **为什么需要它**：umi 4.6 没有 keepalive，路由一变 `AnalysisPage` 真的被卸载，
 * 切回来是全新实例——用户填好的条件全丢，挂载副作用还会把 4 个接口重发一遍。
 * 这份缓存让「切回来」变成从快照复原，而不是重新初始化。
 *
 * **只活在当前页面会话里**：模块级变量，不写 localStorage / sessionStorage。
 * 刷新页面即清空——用户按刷新这个动作本身就表达了「我要重新开始」。
 *
 * **写入是 write-through**：每次状态改动立即落进快照，不在组件卸载时统一写。
 * 卸载时写依赖 effect cleanup 的执行顺序，React 18 并发下不可靠，而它失败是静默的
 * ——用户切回来看到默认值，与本变更要修的那个 bug 长得一模一样，无从区分。
 */

/**
 * 快照结构版本。**改动 `CachedValues` 或 `AnalysisSnapshot` 的形状时必须 +1。**
 * 开发期热更新后会话里可能残留旧形状的快照，字段以 `undefined` 渗进 UI，表现为
 * 零散的空白控件，极难归因；版本号让这种情况退化成「走一次正常初始化」。
 */
export const CACHE_VERSION = 1;

/** 走 `useCachedState` 的那些值。键是字面量联合类型——写错 key 即 TypeScript 报错。 */
export interface CachedValues {
  scope: ScopeDraft;
  filters: FilterDraft;
  appliedView: View;
  appliedSig: string;
  sites: SiteOption[];
  availableDates: string[];
  categories: CategoryOption[];
  filterOpen: boolean;
  resultTotal: number;
  pendingRebuild: boolean;
  sortDraft: { field: string; dir: 'asc' | 'desc' };
}

export type CacheKey = keyof CachedValues;

/** 一次列表请求的完整标识。少任何一项都会让「同一次请求」的判定变松。 */
export interface RequestKey {
  body: SearchBody;
  orderBy: string;
  orderDir: 'asc' | 'desc';
  page: number;
  perPage: number;
}

export interface AnalysisSnapshot {
  version: number;
  values: Partial<CachedValues>;
  /** 生效条件（原 `appliedRef`）。草稿在 `values.scope` / `values.filters`，两份分开存。 */
  applied: SearchBody | null;
  /** 生效排序（原 `appliedSortRef`），与草稿排序 `values.sortDraft` 同样分开 */
  appliedSort: { field: string; dir: 'asc' | 'desc' } | null;
  needsTotal: boolean;
  total: number;
  /** 最近一次真实取数的请求标识，用于 `request` 的短路判定 */
  lastRequest: RequestKey | null;
  rows: AnalysisRow[];
  scrollY: number;
}

type Slots = Omit<AnalysisSnapshot, 'version' | 'values'>;

let snapshot: AnalysisSnapshot | null = null;

const blank = (): AnalysisSnapshot => ({
  version: CACHE_VERSION,
  values: {},
  applied: null,
  appliedSort: null,
  needsTotal: true,
  total: 0,
  lastRequest: null,
  rows: [],
  scrollY: 0,
});

/** 读快照。没有、或版本不匹配时返回 `null`（调用方据此走完整初始化）。 */
export function readSnapshot(): AnalysisSnapshot | null {
  if (!snapshot) return null;
  if (snapshot.version !== CACHE_VERSION) return null;
  return snapshot;
}

export function writeValue<K extends CacheKey>(key: K, value: CachedValues[K]): void {
  const current = snapshot?.version === CACHE_VERSION ? snapshot : blank();
  snapshot = { ...current, values: { ...current.values, [key]: value } };
}

export function writeSlot(patch: Partial<Slots>): void {
  const current = snapshot?.version === CACHE_VERSION ? snapshot : blank();
  snapshot = { ...current, ...patch };
}

/**
 * 清空缓存。**登出时必须调用**：同一标签页内换账号登录后，绝不能看到上一个用户的
 * 查询条件与结果行。
 */
export function clearSnapshot(): void {
  snapshot = null;
}

/** 仅供测试构造异常快照（如版本不匹配）。生产代码不要调用。 */
export function __setRawSnapshotForTest(raw: AnalysisSnapshot | null): void {
  snapshot = raw;
}

/** 键序无关的稳定序列化，供 `sameRequest` 比较条件对象。 */
function stable(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(',')}}`;
}

/**
 * 两次列表请求是否完全相同。
 *
 * **判据必须是完整的请求标识，不能退化成「有没有缓存」**：后者会让用户切回来之后
 * 翻页、改排序都不再去查，那是新的请求，不是同一次。
 */
export function sameRequest(a: RequestKey | null, b: RequestKey | null): boolean {
  if (!a || !b) return false;
  return (
    a.page === b.page &&
    a.perPage === b.perPage &&
    a.orderBy === b.orderBy &&
    a.orderDir === b.orderDir &&
    stable(a.body) === stable(b.body)
  );
}
