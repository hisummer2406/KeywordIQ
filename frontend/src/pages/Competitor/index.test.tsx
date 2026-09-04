import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listGroups, listWatches, createGroup, renameGroup, deleteGroup, updateWatch, removeWatches, getSites } =
  vi.hoisted(() => ({
    listGroups: vi.fn(),
    listWatches: vi.fn(),
    createGroup: vi.fn(),
    renameGroup: vi.fn(),
    deleteGroup: vi.fn(),
    updateWatch: vi.fn(),
    removeWatches: vi.fn(),
    getSites: vi.fn(),
  }));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/competitor', () => ({
  listGroups,
  listWatches,
  createGroup,
  renameGroup,
  deleteGroup,
  updateWatch,
  removeWatches,
}));
vi.mock('@/services/upload', () => ({ getSites }));

import CompetitorPage from './index';

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
    status: 'PENDING_BASELINE',
    created_at: '2026-08-01T00:00:00Z',
    // 默认按「无基线」两态一起给：新关注的卖家两个指标恒为 null，不是 0 或空对象——
    // 与后端 watch_crud 的相关子查询在无基线时返回 NULL（不是 count(*) 的 0）保持一致。
    new_discoveries_7d: null,
    last_reported_at: null,
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

describe('状态列与占位指标', () => {
  it('新关注的卖家显示待建立基线,新发现数是占位符不是 0,最近采集于显示未采集', async () => {
    await renderSettled();
    // 「待建立基线」在状态列与指标列各出现一次，都对应同一个判据（有没有基线），
    // 所以用 getAllByText 而不是 getByText——后者遇到多个匹配会抛错。
    await waitFor(() => expect(screen.getAllByText('待建立基线').length).toBe(2));
    // 0 会被读成"没有新发现",占位符才准确表达"这个指标还没有数据来源"
    expect(screen.getByText('未采集')).toBeTruthy();
  });

  it('已有基线的卖家显示真实数字含 0,与最近采集时刻', async () => {
    listWatches.mockResolvedValue({
      success: true,
      data: [
        watch({
          status: 'MONITORING',
          new_discoveries_7d: 0,
          last_reported_at: new Date().toISOString(), // 刚采集过，不陈旧
        }),
      ],
      total: 1,
      watch_count: 1,
      max_watch: 3000,
    });
    await renderSettled();
    await waitFor(() => expect(screen.getByText('监控中')).toBeTruthy());
    // 真实的 0：不是空白、不是「待建立基线」占位符
    expect(screen.getByText('0')).toBeTruthy();
    expect(screen.queryByText('待建立基线')).toBeNull();
  });

  it('很久没采集的卖家在最近采集于旁边给出陈旧提示', async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    listWatches.mockResolvedValue({
      success: true,
      data: [
        watch({ status: 'MONITORING', new_discoveries_7d: 0, last_reported_at: eightDaysAgo }),
      ],
      total: 1,
      watch_count: 1,
      max_watch: 3000,
    });
    await renderSettled();
    // 陈旧提示用 warning 语义色渲染时刻文本，而不是普通文本——用户要能一眼分辨
    // 「确实没有新发现」与「很久没采过、这个 0 不可信」
    await waitFor(() => {
      const warned = document.querySelector('.ant-typography-warning');
      expect(warned).toBeTruthy();
    });
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
