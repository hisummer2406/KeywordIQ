import type { View } from '@/services/analysis';
import type { FilterDraft } from './filters';

/**
 * 用户保存的默认查询条件（对标卖家精灵的「保存当前模式」）。
 *
 * 分析查询屏的条件初始化有**三级优先级**，高者胜出：
 *
 * 1. **本会话缓存**（`stateCache.ts`，模块级内存）——有缓存就整段跳过初始化、一个请求
 *    都不发。刷新即失效。
 * 2. **用户保存的条件**（本模块，localStorage）——跨登录存活，直到用户点「重置条件」。
 * 3. **内置默认**（`filters.ts` 的 `defaultFilters()`，只看新词）——没保存过时的兜底。
 *
 * **这份必须活过 `doLogout`，与会话快照待遇不同。** `app.tsx` 登出时 `clearSnapshot()`
 * 清会话快照，理由是它是**无主的单份**——同一标签页换账号登录会看到上一个用户的条件与
 * 结果行，只能靠清。而保存的条件按 `userId` 分键，乙登录时读的是乙的键，天然看不到甲的；
 * 清了它反而让「下次登录还是这组」当场不成立，那正是这个功能的全部意义。
 *
 * 取不到 userId 时一律**不读不写**（视同没保存过），绝不回退到某个无主的共享键——
 * 那正是串账号的入口。
 */

/**
 * payload 形状的版本。**改 `SavedMode` 的形状就要 +1。**
 *
 * 版本不符时直接丢弃、不做形状迁移：写一套把旧 payload 升到新形状的代码，需要为每个
 * 历史形状留一份转换器，而收益只是省用户一次「重新保存」。
 *
 * 这条比 `stateCache` 的 `CACHE_VERSION` 更要紧——会话快照最多活一次刷新，而这份能活
 * 几个月、跨好几次上线。旧形状渗进 UI 的表现是零散的空白控件，极难归因。
 */
export const SAVED_MODE_VERSION = 2;

const KEY_PREFIX = 'keywordiq.analysis.savedMode.v1';

export interface SavedMode {
  version: number;
  site: string;
  view: View;
  /**
   * 过滤区的筛选条件。**不含 `q`**——检索框里的关键词/ASIN 是一次性查找，不是「常看
   * 的模式」；存了会让用户每次登录都落在一个窄得只剩几行的结果里，而他早忘了当初搜的
   * 是什么。
   */
  filters: Omit<FilterDraft, 'q'>;
  sort: { field: string; dir: 'asc' | 'desc' };
}

/** 键带 userId：同一浏览器上不同账号互不可见。 */
const keyFor = (userId: number) => `${KEY_PREFIX}.${userId}`;

/** 删除保存的条件。userId 缺失或存储不可写都是空操作，不抛。 */
export function clearSavedMode(userId: number | undefined): void {
  if (userId === undefined) return;
  try {
    window.localStorage.removeItem(keyFor(userId));
  } catch {
    // 删不掉就算了：下次读到旧值时版本校验仍会兜住
  }
}

/**
 * 读保存的条件。以下情况一律返回 `null`（视同没保存过）：
 * userId 缺失、没存过、JSON 损坏、版本不符。后两种会顺手把那条删掉。
 */
export function readSavedMode(userId: number | undefined): SavedMode | null {
  if (userId === undefined) return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(keyFor(userId));
  } catch {
    // 存储不可读（隐私模式等）：当作没保存过，不影响这一屏的其余功能
    return null;
  }
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<SavedMode>;
    if (parsed?.version !== SAVED_MODE_VERSION || !parsed.site || !parsed.view) {
      clearSavedMode(userId);
      return null;
    }
    return parsed as SavedMode;
  } catch {
    clearSavedMode(userId);
    return null;
  }
}

/**
 * 写保存的条件。返回是否写成功——**隐私模式下 `setItem` 会抛**，调用方据此提示用户，
 * 但绝不能让这一屏崩掉。
 */
export function writeSavedMode(userId: number | undefined, mode: Omit<SavedMode, 'version'>): boolean {
  if (userId === undefined) return false;
  try {
    window.localStorage.setItem(
      keyFor(userId),
      JSON.stringify({ ...mode, version: SAVED_MODE_VERSION } satisfies SavedMode),
    );
    return true;
  } catch {
    return false;
  }
}
