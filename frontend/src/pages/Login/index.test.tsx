import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';

const { pushMock, refreshMock, loginMock, historyState, modelStore } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  refreshMock: vi.fn(),
  loginMock: vi.fn(),
  historyState: { search: '' },
  // initialState 的替身：refresh() 往里写 currentUser 并通知订阅者重渲染，
  // 复现真实运行时"刷新→提交→重渲染"这条链路。登录页的跳转正是挂在这条链路末端的。
  modelStore: { currentUser: undefined as unknown, listeners: new Set<() => void>() },
}));

vi.mock('@umijs/max', async () => {
  const React = await vi.importActual<typeof import('react')>('react');
  return {
    history: {
      push: pushMock,
      get location() {
        return { search: historyState.search, pathname: '/login' };
      },
    },
    useModel: () => {
      const [, force] = React.useReducer((n: number) => n + 1, 0);
      React.useEffect(() => {
        modelStore.listeners.add(force);
        return () => {
          modelStore.listeners.delete(force);
        };
      }, []);
      return { initialState: { currentUser: modelStore.currentUser }, refresh: refreshMock };
    },
  };
});
vi.mock('@/services/auth', () => ({ login: loginMock }));

import LoginPage from '@/pages/Login';
import { getToken } from '@/utils/token';

const renderLogin = () =>
  render(
    <App>
      <LoginPage />
    </App>,
  );

async function submit(username = 'admin', password = 'pwd123') {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('用户名'), username);
  await user.type(screen.getByPlaceholderText('密码'), password);
  await user.click(screen.getByRole('button', { name: /登 ?录/ }));
}

/**
 * 登录是唯一的入口屏，跳转错了整个后台进不去，所以这里守的是：令牌落地、
 * initialState 刷新、以及 redirect 参数回跳（鉴权拦截把用户踢回登录时带的那个）。
 */
describe('登录屏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    historyState.search = '';
    loginMock.mockResolvedValue({ token: 'jwt-abc' });
    modelStore.currentUser = undefined;
    modelStore.listeners.clear();
    // 默认：refresh 之后 initialState 带回当前用户（真实成功路径）
    refreshMock.mockImplementation(async () => {
      modelStore.currentUser = { user_name: 'admin' };
      modelStore.listeners.forEach((notify) => notify());
    });
  });

  it('登录成功：存令牌 → 刷新 initialState → 跳分析屏', async () => {
    renderLogin();
    await submit();

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith({ username: 'admin', password: 'pwd123' }));
    expect(getToken()).toBe('jwt-abc');
    expect(refreshMock).toHaveBeenCalled();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/analysis'));
  });

  /**
   * 回归：曾经是 `await refresh()` 后立即 push。refresh 返回只代表刷新发起了，不代表
   * initialState 已提交；抢在提交前跳过去，ProLayout 的 onPageChange 读到旧的
   * currentUser=undefined，把刚登录成功的用户又推回 /login——即"登录成功后闪回登录页"。
   * 所以：currentUser 没到位，就不许跳。
   */
  it('initialState 尚未带回 currentUser 时不跳转', async () => {
    refreshMock.mockImplementation(async () => {
      // 刷新发起了，但状态还没提交（currentUser 仍为空）
    });
    renderLogin();
    await submit();

    await waitFor(() => expect(refreshMock).toHaveBeenCalled());
    expect(pushMock).not.toHaveBeenCalled();
  });

  it('currentUser 到位后补跳，不丢跳转', async () => {
    let commit = () => {};
    refreshMock.mockImplementation(async () => {
      commit = () => {
        modelStore.currentUser = { user_name: 'admin' };
        modelStore.listeners.forEach((notify) => notify());
      };
    });
    renderLogin();
    await submit();

    await waitFor(() => expect(refreshMock).toHaveBeenCalled());
    expect(pushMock).not.toHaveBeenCalled();

    commit(); // 状态迟到提交
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/analysis'));
  });

  it('带 redirect 参数时回跳原目标', async () => {
    historyState.search = '?redirect=/upload';
    renderLogin();
    await submit();

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/upload'));
  });

  it('登录失败：不存令牌、不跳转（错误提示由全局 errorHandler 负责）', async () => {
    loginMock.mockRejectedValue(new Error('401'));
    renderLogin();
    await submit('admin', 'wrong');

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(getToken()).toBeNull();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it('用户名密码为空时不发请求（表单必填校验）', async () => {
    renderLogin();
    await userEvent.setup().click(screen.getByRole('button', { name: /登 ?录/ }));

    await waitFor(() => expect(screen.getByText('请输入用户名')).toBeInTheDocument());
    expect(loginMock).not.toHaveBeenCalled();
  });
});
