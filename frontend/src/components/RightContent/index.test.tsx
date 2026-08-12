import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('@umijs/max', () => ({ history: { push } }));

import RightContent from '@/components/RightContent';

function user(overrides: Partial<API.CurrentUser> = {}): API.CurrentUser {
  return {
    id: 1,
    username: 'alice',
    role: 'operator',
    role_name: '运营',
    permissions: ['analysis:view', 'analysis:export', 'upload:import'],
    is_active: true,
    is_super: false,
    ...overrides,
  } as API.CurrentUser;
}

/** 展开头像下拉。菜单是懒渲染的，不点开读不到菜单项。 */
async function openMenu(currentUser: API.CurrentUser) {
  render(<RightContent currentUser={currentUser} onLogout={vi.fn()} />);
  await act(async () => {
    await userEvent.click(screen.getByText(currentUser.username));
  });
}

/**
 * 用户管理是**不可授予**的能力：判据只有 is_super，与后端 `require_super()` 同源。
 *
 * 这组用例守的是入口的显隐。要强调的是隐藏入口**不是**访问控制——真正拦住的是路由
 * access 守卫和端点依赖；这里只保证不给用户一个点了必然 403 的入口。
 */
describe('顶栏账号区', () => {
  beforeEach(() => vi.clearAllMocks());

  it('超级账号能看到用户管理入口，点击后跳转 /user', async () => {
    await openMenu(user({ is_super: true, role: 'admin', role_name: '超级管理员' }));

    const entry = screen.getByText('用户管理');
    await act(async () => {
      await userEvent.click(entry);
    });

    expect(push).toHaveBeenCalledWith('/user');
  });

  it('普通用户看不到用户管理入口', async () => {
    await openMenu(user());
    expect(screen.queryByText('用户管理')).toBeNull();
  });

  it('admin 角色但非超级账号，同样看不到——判据是 is_super 不是角色码', async () => {
    // 与后端 require_super 的核心回归对应：把角色码算进判据，就等于让「谁能管用户」
    // 变成改角色配置就能改变的事
    await openMenu(user({ role: 'admin', role_name: '超级管理员', is_super: false }));
    expect(screen.queryByText('用户管理')).toBeNull();
  });

  it('未登录时整块不渲染', () => {
    const { container } = render(<RightContent currentUser={undefined} onLogout={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('退出登录始终在，且调用传入的回调', async () => {
    const onLogout = vi.fn();
    render(<RightContent currentUser={user()} onLogout={onLogout} />);
    await act(async () => {
      await userEvent.click(screen.getByText('alice'));
    });
    await act(async () => {
      await userEvent.click(screen.getByText('退出登录'));
    });
    expect(onLogout).toHaveBeenCalled();
  });
});
