import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  listGroups,
  getLibraryScope,
  watchGroup,
  createGroup,
  renameGroup,
  deleteGroup,
  removeItems,
  getSites,
  getKeywordTrend,
} = vi.hoisted(() => ({
  listGroups: vi.fn(),
  getLibraryScope: vi.fn(),
  watchGroup: vi.fn(),
  createGroup: vi.fn(),
  renameGroup: vi.fn(),
  deleteGroup: vi.fn(),
  removeItems: vi.fn(),
  getSites: vi.fn(),
  getKeywordTrend: vi.fn(),
}));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/library', () => ({
  listGroups,
  getLibraryScope,
  watchGroup,
  createGroup,
  renameGroup,
  deleteGroup,
  removeItems,
}));
vi.mock('@/services/upload', () => ({ getSites }));
// 抽屉内部调 getKeywordTrend；不 mock 会走真实实现，命中与 KeywordTrendDrawer 自身
// 测试同样的 esbuild/@umijs/max 环境坑（见该文件顶部注释）。
vi.mock('@/services/keywordTrend', () => ({ getKeywordTrend }));

import KeywordLibraryPage from './index';

const GROUP = {
  group_id: 1,
  site: 'US',
  name: '主推词',
  item_count: 3,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const SCOPE = { site: 'US', view: 'DAY' as const, dates: ['2026-08-15', '2026-08-14'] };

function row(overrides: Record<string, unknown> = {}) {
  return {
    keyword_id: 11,
    keyword: 'wireless earbuds',
    status: 'LISTED',
    added_at: '2026-08-01T00:00:00Z',
    is_dropped: false,
    first_listed: false,
    current_ranking: 12,
    previous_ranking: 15,
    ranking_change: -3,
    top1_click_share: 1,
    top1_conversion_share: 1,
    top2_click_share: 0,
    top2_conversion_share: 0,
    top3_click_share: 0,
    top3_conversion_share: 0,
    trend: [15, 12],
    trend_dates: ['2026-08-14', '2026-08-15'],
    history: [15, null, null],
    ...overrides,
  };
}

async function renderSettled() {
  await act(async () => {
    render(<KeywordLibraryPage />);
  });
}

/** 手动控制 resolve 时机，用来卡住网络往返、模拟"activeId 已变、表格还没重新加载完成"的窗口。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/**
 * 分组管理区（GroupBar）所在的卡片。
 *
 * 分组名会同时出现在两处——GroupBar 的单选按钮标签，以及表格上方作用域条的
 * 「{分组名} · 站点 xx（由分组决定）· 共 N 个词」提示——两处都合法存在，
 * 裸 `getByText(/主推词/)` 因此会撞上"找到多个元素"。「新建分组」按钮只在
 * GroupBar 里出现一次，用它的最近 `.ant-card` 祖先能精确圈定这一块，不依赖
 * 分组名文本本身。
 */
function groupBarCard() {
  return screen.getByRole('button', { name: /新建分组/ }).closest('.ant-card') as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  getSites.mockResolvedValue([{ code: 'US', name: '美国', domain: 'amazon.com' }]);
  listGroups.mockResolvedValue([GROUP]);
  getLibraryScope.mockResolvedValue(SCOPE);
  watchGroup.mockResolvedValue({
    success: true,
    data: [row()],
    total: 1,
    group_id: 1,
    site: 'US',
    view: 'DAY',
    date: '2026-08-15',
  });
});

describe('进屏即查', () => {
  it('挂载后自动查了当前分组，不用点按钮', async () => {
    await renderSettled();
    await waitFor(() => expect(watchGroup).toHaveBeenCalled());
    expect(watchGroup.mock.calls[0][0]).toMatchObject({ group_id: 1 });
  });

  it('分组管理区列出分组，带站点与词数', async () => {
    // 这一屏没有站点选择器，站点只能从分组上读到——漏掉站点，用户就无从判断
    // 表格里的排名属于哪个市场。
    //
    // **分组名与站点/词数不是各自独立的文本节点。** GroupBar 把
    // `{group.name} · {group.item_count} 词` 写在同一个元素里，DOM 里是三个相邻的
    // 兄弟文本节点，会被拼成整串「主推词 · 3 词」——精确匹配 '主推词' 或 '3' 都找不到
    // 任何元素（这不是本次改动引入的：v2 主干当前的 CI 就是这样红的，见
    // `gh run view` 对最近一次合并的 CI 记录）。
    //
    // **改成正则子串匹配后新踩了另一个坑，必须一并说明**：页面自身在分组条下方还有
    // 一句「{分组名} · 站点 {站点} （由分组决定）· 共 N 个词」，同样含有分组名与站点码
    // 这两个子串——不限定查询范围的话，`/主推词/`、`'US'` 都会在两处各命中一次，
    // `getByText` 因命中多个元素直接抛错（`findByText` 的超时包装会把这个"多个匹配"
    // 错误吞掉、重试到超时后再抛出一个更笼统的"找不到"，字面上完全看不出真正原因是
    // "太多个"而不是"零个"，排查时只能靠 `pnpm exec vitest run` 直接跑这个文件看完整
    // 报错）。用 `within` 限定在分组选择器（`.ant-radio-group`）范围内，只问 GroupBar
    // 自己渲染的那一份。
    await renderSettled();
    await waitFor(() => expect(screen.getByRole('button', { name: /新建分组/ })).toBeTruthy());
    // 分组名同时出现在 GroupBar 与表格上方作用域条两处，裸 getByText 会因此撞上
    // "找到多个元素"——scoped 到 GroupBar 卡片本身，见 groupBarCard() 的说明。
    const card = within(groupBarCard());
    expect(card.getByText(/主推词/)).toBeTruthy();
    expect(card.getByText('US')).toBeTruthy();
    expect(card.getByText(/3\s*词/)).toBeTruthy();
  });

  it('站点由分组决定，界面写明这一点', async () => {
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/由分组决定/)).toBeTruthy());
  });
});

describe('三种行状态', () => {
  it('在榜显示排名', async () => {
    await renderSettled();
    await waitFor(() => expect(screen.getByText('在榜')).toBeTruthy());
  });

  it('掉榜标明状态', async () => {
    watchGroup.mockResolvedValue({
      success: true,
      total: 1,
      group_id: 1,
      site: 'US',
      view: 'DAY',
      date: '2026-08-15',
      data: [
        row({
          status: 'DROPPED',
          is_dropped: true,
          current_ranking: null,
          last_seen_date: '2026-08-14',
          last_ranking: 40,
        }),
      ],
    });
    await renderSettled();
    // 状态列与现排名列各有一处「已掉榜」——两处都在，说明列复用没有把标签吃掉
    await waitFor(() => expect(screen.getAllByText('已掉榜').length).toBeGreaterThan(0));
  });

  it('无数据的行明说该期无数据，而不是渲染成空排名', async () => {
    watchGroup.mockResolvedValue({
      success: true,
      total: 1,
      group_id: 1,
      site: 'US',
      view: 'DAY',
      date: '2026-08-15',
      data: [row({ status: 'MISSING', current_ranking: null, trend: [], trend_dates: [], history: [] })],
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText('该期无数据')).toBeTruthy());
  });
});

describe('空状态', () => {
  it('空分组说的是「还没有词」并指路分析屏', async () => {
    watchGroup.mockResolvedValue({
      success: true,
      data: [],
      total: 0,
      group_id: 1,
      site: 'US',
      view: 'DAY',
      date: '2026-08-15',
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/这个分组还没有词/)).toBeTruthy());
  });

  it('站点一期数据都没有时，说的是没有数据而不是清单为空', async () => {
    getLibraryScope.mockResolvedValue({ site: 'US', view: 'DAY', dates: [] });
    watchGroup.mockResolvedValue({
      success: true,
      data: [],
      total: 0,
      group_id: 1,
      site: 'US',
      view: 'DAY',
      date: null,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/还没有导入任何数据/)).toBeTruthy());
  });

  it('一个分组都没有时，表格说的是「先建一个分组」而不是「这个分组还没有词」', async () => {
    // 「这个分组还没有词」在没有分组时是错的：它把用户引向去加词，而他缺的是先建组。
    listGroups.mockResolvedValue([]);
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/先在上面新建一个分组/)).toBeTruthy());
    expect(screen.queryByText(/这个分组还没有词/)).toBeNull();
  });
});

describe('入库入口不在这一屏', () => {
  it('界面上没有新增关键词的入口', async () => {
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/由分组决定/)).toBeTruthy());
    // 「管理分组」管的是分组，不是词；不应出现任何加词/粘贴录入的按钮
    expect(screen.queryByRole('button', { name: /加词|添加关键词|粘贴/ })).toBeNull();
  });
});

describe('横向宽度', () => {
  it('分组管理区横铺在表格上方，不做侧栏', async () => {
    // 侧栏形态下盯盘表格有近 400px 被裁进横向滚动（1920 视口实测）。分组一览有价值，
    // 但代价必须落在纵向：纵向可以滚，横向被裁掉的列是找不回来的。
    await renderSettled();
    await waitFor(() => expect(screen.getByRole('button', { name: /新建分组/ })).toBeTruthy());

    const bar = groupBarCard();
    const table = document.querySelector('.ant-table') as HTMLElement;
    // 管理区与表格是上下两块（同一个父容器里的兄弟节点），不是左右分栏
    expect(bar.contains(table)).toBe(false);
  });
});

describe('操作列链接的站点不能领先于表格实际加载的数据', () => {
  // 切分组时 activeId 先于表格重新加载完成变化：setActiveId 是同步的，reloadScope
  // 与随后的 reload(true) 都要等一轮网络往返。这段窗口内若操作列链接跟着新 activeId
  // 走，会把仍显示的旧分组的词按新站点拼链接，跳到错误地区（Codex 审核 PR #85）。
  it('切分组的网络往返还没完成时，操作列仍按当前显示的行的站点拼链接', async () => {
    const GROUP_DE = {
      group_id: 2,
      site: 'DE',
      name: '德语词',
      item_count: 1,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    };
    listGroups.mockResolvedValue([GROUP, GROUP_DE]);
    getSites.mockResolvedValue([
      { code: 'US', name: '美国', domain: 'amazon.com' },
      { code: 'DE', name: '德国', domain: 'amazon.de' },
    ]);

    await renderSettled();
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(1));

    const trendsLink = () => screen.getByRole('link', { name: /查看 Google 搜索趋势/ }) as HTMLAnchorElement;
    await waitFor(() => expect(trendsLink().href).toContain('geo=US'));

    // 卡住这次切组触发的 getLibraryScope，制造"activeId 已经是 DE、表格还没重新
    // 加载完成"的窗口
    const scopeDeferred = deferred<{ site: string; view: 'DAY'; dates: string[] }>();
    getLibraryScope.mockReturnValueOnce(scopeDeferred.promise);

    await act(async () => {
      fireEvent.click(screen.getByRole('radio', { name: /德语词/ }));
    });

    // 这段窗口内 watchGroup 还没被再次调用，表格里仍是 US 那批行——链接必须仍指向 US，
    // 不能因为 activeId 已经变成 DE 就提前跳过去
    expect(watchGroup).toHaveBeenCalledTimes(1);
    expect(trendsLink().href).toContain('geo=US');

    watchGroup.mockResolvedValueOnce({
      success: true,
      data: [row({ keyword: 'kabel' })],
      total: 1,
      group_id: 2,
      site: 'DE',
      view: 'DAY',
      date: '2026-08-15',
    });
    await act(async () => {
      scopeDeferred.resolve({ site: 'DE', view: 'DAY', dates: ['2026-08-15'] });
    });

    // 网络往返完成、表格重新加载出 DE 那批行之后，链接才跟着变成 DE
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(trendsLink().href).toContain('geo=DE'));
  });
});

describe('详情抽屉不能领先于表格实际加载的视图/日期', () => {
  // 切视图时 view 状态先于表格重新加载完成变化：setView 是同步的，reloadScope 与随后的
  // reload(true) 都要等一轮网络往返。这段窗口内若详情抽屉跟着新 view/最新 date 状态走，
  // 会把仍在渲染的旧行（DAY）的 keyword_id 配上新视图（WEEK）解析，把 DAY 历史当成 WEEK
  // 历史显示（PR #87 复审 P2）。
  it('切视图的网络往返还没完成时，点开的详情仍用当前显示的行所属的视图与日期', async () => {
    getKeywordTrend.mockResolvedValue({
      success: true,
      keyword_id: 11,
      keyword: 'wireless earbuds',
      site: 'US',
      view: 'DAY',
      range: 'RECENT_30D',
      on: '2026-08-15',
      coverage: { start: '2026-08-09', end: '2026-08-15' },
      points: [{ date: '2026-08-15', status: 'RANKED', ranking: 12 }],
      best_ranking: { date: '2026-08-15', ranking: 12 },
      latest_available: { date: '2026-08-15', ranking: 12 },
    });

    await renderSettled();
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(1));

    // 卡住这次切视图触发的 getLibraryScope，制造"view 已经是 WEEK、表格还没重新
    // 加载完成"的窗口
    const scopeDeferred = deferred<{ site: string; view: 'WEEK'; dates: string[] }>();
    getLibraryScope.mockReturnValueOnce(scopeDeferred.promise);

    await act(async () => {
      fireEvent.click(screen.getByRole('radio', { name: '按周' }));
    });

    // 这段窗口内 watchGroup 还没被再次调用，表格里仍是 DAY 那批行
    expect(watchGroup).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(screen.getByTitle('查看关键词 ABA 排名趋势详情'));
    });

    // 抽屉必须按这批行实际所属的 DAY/2026-08-15 取数，不能用已经变成 WEEK 的 view 状态
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalled());
    expect(getKeywordTrend.mock.calls[0][0]).toMatchObject({ view: 'DAY', on: '2026-08-15' });

    // 制造"表格随后真的切到了 WEEK"这一步——默认的 watchGroup mock 固定回 DAY，这里
    // 显式让下一次调用回 WEEK，才能观察到 loadedView 真的变化之后抽屉是否跟着变。
    watchGroup.mockResolvedValueOnce({
      success: true,
      data: [row()],
      total: 1,
      group_id: 1,
      site: 'US',
      view: 'WEEK',
      date: '2026-08-11',
    });

    await act(async () => {
      scopeDeferred.resolve({ site: 'US', view: 'WEEK', dates: ['2026-08-11'] });
    });
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(2));

    // 抽屉在打开那一刻已经把 DAY/2026-08-15 冻结进 trendTarget——loadedView 事后真的
    // 变成 WEEK 不应该让抽屉重新以 WEEK 发请求（PR #87 复审第二轮 P2：第一版修复只护住
    // 了"点击当下读到的值"，没护住"点击之后 loadedView 才变化"这一步）。
    expect(getKeywordTrend).toHaveBeenCalledTimes(1);
  });
});

describe('表格 request 乱序落地时不倒灌覆盖较新请求的作用域', () => {
  // ProTable 允许同一时刻有多个 request 调用同时在飞——旧调用不会被真的取消。
  // 若一次更早发出的调用（切到 DE 那次）在一次更新的调用（切回 US 那次）之后才落地，
  // request 里若不核对调用序号，仍会用这次旧响应把 loadedSite 改回 DE，操作列/详情
  // 抽屉却明明该跟着表格当下实际展示的 US 行走（PR #87 第五轮 P2）。
  it('切分组两次、较早那次请求较晚落地时，不倒灌覆盖较新请求的站点', async () => {
    const GROUP_DE = {
      group_id: 2,
      site: 'DE',
      name: '德语词',
      item_count: 1,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    };
    listGroups.mockResolvedValue([GROUP, GROUP_DE]);
    getSites.mockResolvedValue([
      { code: 'US', name: '美国', domain: 'amazon.com' },
      { code: 'DE', name: '德国', domain: 'amazon.de' },
    ]);
    getKeywordTrend.mockResolvedValue({
      success: true,
      keyword_id: 11,
      keyword: 'wireless earbuds',
      site: 'US',
      view: 'DAY',
      range: 'RECENT_30D',
      on: '2026-08-15',
      coverage: { start: '2026-08-09', end: '2026-08-15' },
      points: [{ date: '2026-08-15', status: 'RANKED', ranking: 12 }],
      best_ranking: { date: '2026-08-15', ranking: 12 },
      latest_available: { date: '2026-08-15', ranking: 12 },
    });

    await renderSettled();
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(1)); // 首屏：US

    // 切到 DE：这次请求（较早发出）先卡住，稍后才落地
    const olderCall = deferred<Awaited<ReturnType<typeof watchGroup>>>();
    watchGroup.mockReturnValueOnce(olderCall.promise);

    await act(async () => {
      fireEvent.click(screen.getByRole('radio', { name: /德语词/ }));
    });
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(2));

    // 再切回主推词（US）：这次（较新发出）用默认 mock，立即落地
    await act(async () => {
      fireEvent.click(screen.getByRole('radio', { name: /主推词/ }));
    });
    await waitFor(() => expect(watchGroup).toHaveBeenCalledTimes(3));

    // 较早那次（DE）现在才落地——响应里仍带一行，只是站点标成 DE，用来隔离 ProTable
    // 自身会不会采纳这份陈旧响应去渲染表格（不是本条要守的范围），单独验证 loadedSite
    // 有没有被这次陈旧响应倒灌覆盖。
    await act(async () => {
      olderCall.resolve({
        success: true,
        data: [row({ keyword: 'kabel' })],
        total: 1,
        group_id: 2,
        site: 'DE',
        view: 'DAY',
        date: '2026-08-15',
      });
    });

    await act(async () => {
      fireEvent.click(screen.getByTitle('查看关键词 ABA 排名趋势详情'));
    });

    // 必须仍是较新那次请求（US）的作用域，不能被较早却后落地的 DE 响应改回去
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalled());
    expect(getKeywordTrend.mock.calls[0][0]).toMatchObject({ site: 'US', on: '2026-08-15' });
  });
});
