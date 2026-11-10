import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  listGroups,
  listWatches,
  createGroup,
  renameGroup,
  deleteGroup,
  updateWatch,
  removeWatches,
  listSellerAsins,
  getSites,
  setSearchParams,
  historyPush,
  searchState,
} = vi.hoisted(() => ({
    listGroups: vi.fn(),
    listWatches: vi.fn(),
    createGroup: vi.fn(),
    renameGroup: vi.fn(),
    deleteGroup: vi.fn(),
    updateWatch: vi.fn(),
    removeWatches: vi.fn(),
    listSellerAsins: vi.fn(),
    getSites: vi.fn(),
    setSearchParams: vi.fn(),
    historyPush: vi.fn(),
    // 店铺类型写在 URL 里；测试里用一个可改的 URLSearchParams 模拟当前地址栏
    searchState: { params: new URLSearchParams() },
  }));

vi.mock('@umijs/max', () => ({
  useAccess: () => ({}),
  useSearchParams: () => [searchState.params, setSearchParams],
  history: { push: historyPush },
}));
// Shopify 那张表有自己的测试（StoreTab.test.tsx），这里只验证类型切换把它换上来
vi.mock('./StoreTab', () => ({ default: () => <div>Shopify 店铺表</div> }));
vi.mock('@/services/competitor', () => ({
  listGroups,
  listWatches,
  createGroup,
  renameGroup,
  deleteGroup,
  updateWatch,
  removeWatches,
  listSellerAsins,
}));
vi.mock('@/services/upload', () => ({ getSites }));

import CompetitorPage from './Stores';

const GROUP = {
  group_id: 1,
  name: '头部竞对',
  watch_count: 2,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

function watch(overrides: Record<string, unknown> = {}) {
  return {
    watch_id: 11,
    seller_id: 'A1TZ946F8HZTNY',
    site: 'US',
    group_id: 1,
    group_name: '头部竞对',
    priority: 1,
    note: null,
    status: 'NOT_COLLECTED',
    created_at: '2026-08-01T00:00:00Z',
    // 默认按「未采集」给：新关注的卖家各指标恒为 null，不是 0 或空对象——
    // 与后端 watch_crud 的相关子查询在无基线时返回 NULL（不是 count(*) 的 0）保持一致。
    new_discoveries_7d: null,
    last_reported_at: null,
    last_report_item_count: null,
    seller_name: null,
    ...overrides,
  };
}

async function renderSettled() {
  await act(async () => {
    render(<CompetitorPage />);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  searchState.params = new URLSearchParams();
  getSites.mockResolvedValue([{ code: 'US', name_zh: '美国', domain: 'amazon.com' }]);
  listGroups.mockResolvedValue([GROUP]);
  listWatches.mockResolvedValue({
    success: true,
    data: [watch()],
    total: 1,
    watch_count: 2,
    max_watch: 3000,
  });
});

describe('进屏即查', () => {
  it('挂载后自动查了「全部关注」,不用点按钮', async () => {
    await renderSettled();
    await waitFor(() => expect(listWatches).toHaveBeenCalled());
    expect(listWatches.mock.calls[0][0]).toMatchObject({ group_id: null });
  });

  it('配额条显示已关注数与上限', async () => {
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/3000/)).toBeTruthy());
    expect(screen.getByText(/已关注/)).toBeTruthy();
    // 「2」同时出现在「全部关注」分组芯片的计数上,故用 getAllByText
    expect(screen.getAllByText('2').length).toBeGreaterThan(0);
  });
});

describe('分组区', () => {
  it('列出分组,带「全部关注」伪分组', async () => {
    // 「头部竞对」同时出现在分组芯片与表格行的分组下拉选中项里(行的 group_name 恰好
    // 同名),故用 getAllByText。
    await renderSettled();
    await waitFor(() => expect(screen.getByRole('radio', { name: /头部竞对/ })).toBeTruthy());
    expect(screen.getByRole('radio', { name: /全部关注/ })).toBeTruthy();
  });

  it('分组管理区横铺在表格上方,不做侧栏', async () => {
    await renderSettled();
    await waitFor(() => expect(document.querySelector('.ant-card-head-title')).toBeTruthy());
    // 「分组」卡片标题在 .ant-card-head 里,与表格列头「分组」不是同一个节点
    const bar = document
      .querySelector('.ant-card-head-title')!
      .closest('.ant-card') as HTMLElement;
    const table = document.querySelector('.ant-table') as HTMLElement;
    expect(bar.contains(table)).toBe(false);
  });

  it('改名和删除收进分组的操作菜单，删除仍会二次确认', async () => {
    await renderSettled();
    const group = await screen.findByRole('radio', { name: /头部竞对/ });
    await act(async () => {
      group.click();
    });
    const actions = await screen.findByRole('button', { name: '当前分组操作' });
    await act(async () => {
      actions.click();
    });
    const remove = await screen.findByText('删除分组');
    expect(screen.getByText('重命名')).toBeTruthy();
    await act(async () => {
      remove.click();
    });
    expect(screen.getByText(/组内 2 个卖家会变为未分组/)).toBeTruthy();
    expect(deleteGroup).not.toHaveBeenCalled();
  });
});

describe('状态列：手动采集与三态（#166）', () => {
  it('新关注的卖家显示未采集与手动采集，新发现数是占位符不是 0', async () => {
    await renderSettled();
    // 「未采集」在状态列与最近采集列各出现一次，都对应同一个判据（有没有基线）
    await waitFor(() => expect(screen.getAllByText('未采集').length).toBe(2));
    expect(screen.getByText('手动采集')).toBeTruthy();
    // 不能出现会被读成后台在自动检查的「监控中」
    expect(screen.queryByText('监控中')).toBeNull();
    expect(screen.queryByText('0')).toBeNull();
  });

  it('已采集的卖家显示真实数字含 0、最近采集时刻与「不代表全店总量」的上报条数', async () => {
    listWatches.mockResolvedValue({
      success: true,
      data: [
        watch({
          status: 'COLLECTED',
          new_discoveries_7d: 0,
          last_reported_at: new Date().toISOString(),
          last_report_item_count: 480,
        }),
      ],
      total: 1,
      watch_count: 1,
      max_watch: 3000,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText('已采集')).toBeTruthy());
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.getByText('上报 480 条（不代表全店总量）')).toBeTruthy();
  });

  it('数据陈旧由后端判定，时刻用 warning 语义色提示', async () => {
    listWatches.mockResolvedValue({
      success: true,
      data: [
        watch({
          status: 'STALE',
          new_discoveries_7d: 0,
          last_reported_at: '2026-08-01T00:00:00Z',
          last_report_item_count: 60,
        }),
      ],
      total: 1,
      watch_count: 1,
      max_watch: 3000,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText('数据陈旧')).toBeTruthy());
    expect(document.querySelector('.ant-typography-warning')).toBeTruthy();
  });

  it('有店名时显示店名并保留 seller_id，没有时回落 seller_id', async () => {
    listWatches.mockResolvedValue({
      success: true,
      data: [watch({ seller_name: 'Acme Store' }), watch({ watch_id: 12, seller_id: 'A2NONAME00001' })],
      total: 2,
      watch_count: 2,
      max_watch: 3000,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText('Acme Store')).toBeTruthy());
    expect(screen.getByText('A1TZ946F8HZTNY')).toBeTruthy();
    expect(screen.getByText('A2NONAME00001')).toBeTruthy();
  });

  it('「看新商品」带着卖家和站点跳到商品发现', async () => {
    await renderSettled();
    const link = await screen.findByText('看新商品');
    await act(async () => {
      link.click();
    });
    expect(historyPush).toHaveBeenCalledWith('/competitor/discovery?seller=A1TZ946F8HZTNY&site=US');
  });
});

describe('店铺类型切换（写在 URL 里）', () => {
  it('缺省是 Amazon 卖家', async () => {
    await renderSettled();
    await waitFor(() => expect(listWatches).toHaveBeenCalled());
    expect(screen.queryByText('Shopify 店铺表')).toBeNull();
  });

  it('URL 带 type=shopify 时直接显示 Shopify 店铺表，刷新不丢', async () => {
    searchState.params = new URLSearchParams('type=shopify');
    await renderSettled();
    expect(await screen.findByText('Shopify 店铺表')).toBeTruthy();
  });

  it('点「Shopify 店铺」把类型写进 URL', async () => {
    await renderSettled();
    const option = await screen.findByText('Shopify 店铺');
    await act(async () => {
      option.click();
    });
    expect(setSearchParams).toHaveBeenCalledWith({ type: 'shopify' });
  });
});

describe('空状态', () => {
  it('全部关注为空时指路「添加卖家」', async () => {
    listWatches.mockResolvedValue({
      success: true, data: [], total: 0, watch_count: 0, max_watch: 3000,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText(/还没有关注任何卖家/)).toBeTruthy());
  });

  it('选中某个分组为空时说的是这个分组没有卖家', async () => {
    listWatches.mockResolvedValue({
      success: true, data: [], total: 0, watch_count: 0, max_watch: 3000,
    });
    await renderSettled();
    const chip = await screen.findByRole('radio', { name: /头部竞对/ });
    await act(async () => {
      chip.click();
    });
    await waitFor(() => expect(screen.getByText('这个分组还没有卖家')).toBeTruthy());
  });
});

describe('添加卖家入口', () => {
  it('顶部有「添加卖家」按钮,点击打开导入弹窗', async () => {
    await renderSettled();
    const button = await screen.findByRole('button', { name: '添加卖家' });
    await act(async () => {
      button.click();
    });
    await waitFor(() => expect(screen.getByText('粘贴文本')).toBeTruthy());
    expect(screen.getByText('导入书签')).toBeTruthy();
  });
});
