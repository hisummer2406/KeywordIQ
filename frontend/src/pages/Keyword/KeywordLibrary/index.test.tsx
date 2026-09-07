import { act, render, screen, waitFor, within } from '@testing-library/react';
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
    const groupPicker = await waitFor(() => {
      const el = document.querySelector('.ant-radio-group');
      if (!el) throw new Error('分组选择器还没渲染出来');
      return el as HTMLElement;
    });
    const chip = within(groupPicker).getByText(/主推词/);
    expect(chip.textContent).toContain('3');
    expect(within(groupPicker).getByText('US')).toBeTruthy();
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
    // 定位管理区用「新建分组」按钮而不是分组名文本：后者依赖分组列表异步加载完成
    // 才会出现，前者与分组数据无关、渲染即挂载，断言与"数据有没有加载完"这件事无关，
    // 只回答"管理区和表格是不是两块独立的容器"。
    const newGroupButton = await screen.findByRole('button', { name: /新建分组/ });

    const bar = newGroupButton.closest('.ant-card') as HTMLElement;
    const table = document.querySelector('.ant-table') as HTMLElement;
    // 管理区与表格是上下两块（同一个父容器里的兄弟节点），不是左右分栏
    expect(bar.contains(table)).toBe(false);
  });
});
