import { vi } from 'vitest';

/**
 * 分析查询屏各测试文件共用的替身与默认数据。
 *
 * **这个模块不许 import 任何业务代码。** 它被 `vi.mock` 的工厂读取，而工厂是在
 * `@/pages/Analysis` 首次 import 服务层时才执行的——若这里再反向 import 页面组件，
 * 就形成 `mocks → 页面 → 服务 → 工厂 → mocks` 的环，工厂拿到的是尚未初始化的绑定
 * （TDZ），报错信息只会说某个变量 "Cannot access before initialization"，指不到这里。
 * 需要页面组件的帮手放在 `analysisHarness.tsx`，那个模块可以随便 import。
 *
 * 为什么不用 `vi.hoisted`：这些替身现在要跨文件共用，而 `vi.hoisted` 的产物是文件内的
 * 局部绑定。改成普通模块导出后，各文件的 `vi.mock` 工厂直接引用它们即可——模块求值
 * 顺序保证了工厂执行时它们已经初始化（本模块无依赖，必然先于页面组件求值）。
 */

export const accessState = { canExport: true, canViewToolbox: true };

/** 当前登录用户。保存的默认条件按 id 分键，改这里就等于换账号。 */
export const userState = { currentUser: { id: 1 } as { id: number } | undefined };

export const searchAnalysis = vi.fn();
export const getScope = vi.fn();
export const getCategories = vi.fn();
export const getBrands = vi.fn();
export const getSites = vi.fn();
export const exportAnalysis = vi.fn();
export const listGroups = vi.fn();
export const createGroup = vi.fn();
export const addItems = vi.fn();

export const ROW = {
  keyword_id: 1,
  keyword: 'coffee maker',
  current_ranking: 1,
  previous_ranking: 4,
  ranking_change: 3,
  is_new: false,
  top1_asin: 'B00TEST001',
  top1_title: 'Some Product',
  top1_brand: 'ACME',
  top1_category: 'Home',
  top1_click_share: 25,
  top1_conversion_share: 10,
  trend: [5, 4, 3, 2, 1],
  trend_dates: ['2026-07-14', '2026-07-15', '2026-07-16', '2026-07-17', '2026-07-18'],
};

/**
 * 每个用例前的复位。**四个测试文件必须都调用它**，少一个就会串。
 *
 * 会话缓存是模块级的、保存的条件在 localStorage 里，两者都跨用例存活。不清的话前一个
 * 用例留下的状态会让后面每个用例的挂载都被当成「切走再切回」——初始化整段跳过，断言
 * 看到的是一屏空数据，而报错信息（找不到某个按钮）完全指不到这个原因。
 */
export function seedAnalysisMocks(): void {
  vi.clearAllMocks();
  userState.currentUser = { id: 1 };
  accessState.canExport = true;
  accessState.canViewToolbox = true;
  listGroups.mockResolvedValue([
    {
      group_id: 7,
      site: 'US',
      name: '主推词',
      item_count: 3,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    },
  ]);
  addItems.mockResolvedValue({ added: 1, existed: 0, item_count: 4 });
  getSites.mockResolvedValue([
    { code: 'US', name_zh: '美国', domain: 'amazon.com' },
    { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
  ]);
  getScope.mockImplementation(async (site?: string) =>
    site === 'DE'
      ? { site: 'DE', view: 'DAY', latest_date: '2026-07-16', available_dates: ['2026-07-16'] }
      : {
          site: 'US',
          view: 'DAY',
          latest_date: '2026-07-18',
          available_dates: ['2026-07-18', '2026-07-17'],
        },
  );
  getBrands.mockResolvedValue([
    { brand: 'ACME', count: 9 },
    { brand: 'Anker', count: 4 },
  ]);
  getCategories.mockResolvedValue([
    { category: 'Home', count: 12 },
    { category: 'Electronics', count: 7 },
  ]);
  searchAnalysis.mockResolvedValue({
    success: true,
    data: [ROW],
    total: 1,
    site: 'US',
    view: 'DAY',
    date: '2026-07-18',
  });
  exportAnalysis.mockResolvedValue(undefined);
}
