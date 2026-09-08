import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listGroups, getLibraryScope, watchGroup, createGroup, renameGroup, deleteGroup, removeItems, getSites } =
  vi.hoisted(() => ({
    listGroups: vi.fn(),
    getLibraryScope: vi.fn(),
    watchGroup: vi.fn(),
    createGroup: vi.fn(),
    renameGroup: vi.fn(),
    deleteGroup: vi.fn(),
    removeItems: vi.fn(),
    getSites: vi.fn(),
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
    is_new: false,
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
    expect(screen.getByRole('button', { name: /新建分组/ })).toBeTruthy();
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
