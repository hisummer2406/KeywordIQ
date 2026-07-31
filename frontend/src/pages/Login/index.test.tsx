import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';

const { pushMock, refreshMock, loginMock, historyState } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  refreshMock: vi.fn(async () => undefined),
  loginMock: vi.fn(),
  historyState: { search: '' },
}));

vi.mock('@umijs/max', () => ({
  history: {
    push: pushMock,
    get location() {
      return { search: historyState.search, pathname: '/login' };
    },
  },
  useModel: () => ({ refresh: refreshMock }),
}));
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
  });

  it('登录成功：存令牌 → 刷新 initialState → 跳分析屏', async () => {
    renderLogin();
    await submit();

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith({ username: 'admin', password: 'pwd123' }));
    expect(getToken()).toBe('jwt-abc');
    // 必须在跳转前刷新，否则 onPageChange 拿不到 currentUser 会把用户踢回登录页
    expect(refreshMock).toHaveBeenCalled();
    expect(pushMock).toHaveBeenCalledWith('/analysis');
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
