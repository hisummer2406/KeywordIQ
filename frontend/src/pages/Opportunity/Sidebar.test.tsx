import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { Modal } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ResearchSummary, Workspace } from '@/services/opportunityIp';
import Sidebar from './Sidebar';

/**
 * 机会中心页内左栏（change adopt-antdx-opportunity-ui design D4）：导航、工作空间、专家任务三组
 * 会话列表，切换、重命名、删除工作空间与加载更多的行为与换壳前一致。
 */

// services 引入 @umijs/max 的 request；不 mock 会把 Umi 打包器拉进 jsdom
vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));

const api = vi.hoisted(() => ({
  listResearches: vi.fn(),
  createWorkspace: vi.fn(),
  renameWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
}));
vi.mock('@/services/opportunityIp', async (orig) => ({
  ...(await orig<typeof import('@/services/opportunityIp')>()),
  ...api,
}));

const ws = (id: number, name: string, task_count: number): Workspace => ({ id, name, task_count, created_at: '' });
const research = (id: number, name: string, over: Partial<ResearchSummary> = {}): ResearchSummary => ({
  id,
  product_name: name,
  markets: ['US'],
  jurisdictions: ['US'],
  workspace_id: null,
  created_at: '',
  candidate_count: 3,
  reviewed_markets: 0,
  last_task: { kind: 'search', status: 'completed' },
  schedule_enabled: null,
  ...over,
});

const onNavigate = vi.fn();
const onWorkspacesChanged = vi.fn();

async function mount(over: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  await act(async () => {
    render(
      <Sidebar
        view="dossiers"
        taskId={null}
        scope="all"
        workspaces={[ws(3, '户外', 0), ws(4, '宠物', 2)]}
        counts={{ total: 5, unassigned: 1 }}
        version={0}
        onNavigate={onNavigate}
        onWorkspacesChanged={onWorkspacesChanged}
        {...over}
      />,
    );
  });
}

// Modal.confirm 是静态调用，挂在 body 上，不随 RTL 的 cleanup 卸载；不清会串到下一个用例
afterEach(() => {
  Modal.destroyAll();
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  onNavigate.mockReset();
  onWorkspacesChanged.mockReset();
  api.listResearches.mockResolvedValue({ items: [research(11, '折叠杯'), research(12, '宠物碗')], total: 2 });
});

describe('导航与切换', () => {
  it('点导航项、工作空间、任务各自上报导航', async () => {
    await mount();
    fireEvent.click(screen.getByText('线索'));
    expect(onNavigate).toHaveBeenLastCalledWith({ view: 'leads' });
    fireEvent.click(screen.getByText('专家'));
    expect(onNavigate).toHaveBeenLastCalledWith({ view: 'experts', task: null });
    fireEvent.click(screen.getByText('宠物'));
    expect(onNavigate).toHaveBeenLastCalledWith({ scope: 4 });
    fireEvent.click(screen.getByText('未归类'));
    expect(onNavigate).toHaveBeenLastCalledWith({ scope: 'none' });
    fireEvent.click(screen.getByText('折叠杯'));
    expect(onNavigate).toHaveBeenLastCalledWith({ view: 'task', task: 11 });
  });

  it('任务按当前范围请求，且显示状态与候选数', async () => {
    await mount({ scope: 4 });
    expect(api.listResearches).toHaveBeenCalledWith({ page: 1, page_size: 30, workspace_id: 4 });
    expect(screen.getAllByText(/执行检索 已完成/).length).toBe(2);
    expect(screen.getAllByText(/候选 3/).length).toBe(2);
  });

  it('还有更多时显示加载更多，点击取下一页', async () => {
    api.listResearches.mockResolvedValueOnce({ items: [research(11, '折叠杯')], total: 2 });
    await mount();
    api.listResearches.mockResolvedValueOnce({ items: [research(12, '宠物碗')], total: 2 });
    await act(async () => {
      fireEvent.click(screen.getByText('加载更多'));
    });
    expect(api.listResearches).toHaveBeenLastCalledWith({ page: 2, page_size: 30 });
    expect(screen.getByText('宠物碗')).toBeTruthy();
  });
});

describe('工作空间管理', () => {
  it('新建工作空间：保存后切到新空间并通知刷新', async () => {
    api.createWorkspace.mockResolvedValue(ws(9, '新空间', 0));
    await mount();
    fireEvent.click(screen.getByText('新建工作空间'));
    fireEvent.change(await screen.findByPlaceholderText(/户外水杯/), { target: { value: '新空间' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    });
    expect(api.createWorkspace).toHaveBeenCalledWith('新空间');
    expect(onNavigate).toHaveBeenLastCalledWith({ scope: 9 });
    expect(onWorkspacesChanged).toHaveBeenCalled();
  });

  async function openMenu(name: string) {
    const item = screen.getByText(name).closest('li') as HTMLElement;
    // 条目里唯一的按钮就是「更多」菜单触发器
    const trigger = within(item).getAllByRole('img').find((i) => i.getAttribute('aria-label') === 'ellipsis') as HTMLElement;
    await act(async () => {
      fireEvent.click(trigger);
    });
  }

  it('重命名工作空间', async () => {
    api.renameWorkspace.mockResolvedValue(undefined);
    await mount();
    await openMenu('户外');
    await act(async () => {
      fireEvent.click(await screen.findByText('重命名'));
    });
    const input = (await screen.findByPlaceholderText(/户外水杯/)) as HTMLInputElement;
    expect(input.value).toBe('户外');
    fireEvent.change(input, { target: { value: '户外用品' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    });
    expect(api.renameWorkspace).toHaveBeenCalledWith(3, '户外用品');
  });

  it('里面还有任务的工作空间不能删除', async () => {
    await mount();
    await openMenu('宠物');
    await act(async () => {
      fireEvent.click(await screen.findByText('删除'));
    });
    const dialog = (await screen.findByText(/里面还有任务/)).closest('[role="dialog"]') as HTMLElement;
    expect((within(dialog).getByRole('button', { name: /删\s*除/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.deleteWorkspace).not.toHaveBeenCalled();
  });

  it('空工作空间可以删除，删除当前范围时回到全部', async () => {
    api.deleteWorkspace.mockResolvedValue(undefined);
    await mount({ scope: 3 });
    await openMenu('户外');
    await act(async () => {
      fireEvent.click(await screen.findByText('删除'));
    });
    // 只在本次弹窗里找按钮（上一个用例的静态弹窗退场动画可能还没结束）
    const dialog = (await screen.findByText(/只删除这个分组/)).closest('[role="dialog"]') as HTMLElement;
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: /删\s*除/ }));
    });
    expect(api.deleteWorkspace).toHaveBeenCalledWith(3);
    expect(onNavigate).toHaveBeenLastCalledWith({ scope: 'all' });
  });
});
