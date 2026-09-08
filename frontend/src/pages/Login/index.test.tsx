import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';

const {
  pushMock,
  refreshMock,
  loginMock,
  ssoStatusMock,
  ssoAuthorizeUrlMock,
  hardRedirectMock,
  historyState,
  modelStore,
} = vi.hoisted(() => ({
  pushMock: vi.fn(),
  refreshMock: vi.fn(),
  loginMock: vi.fn(),
  ssoStatusMock: vi.fn(),
  ssoAuthorizeUrlMock: vi.fn(),
  hardRedirectMock: vi.fn(),
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
vi.mock('@/services/auth', () => ({
  login: loginMock,
  ssoStatus: ssoStatusMock,
  ssoAuthorizeUrl: ssoAuthorizeUrlMock,
}));
vi.mock('@/utils/redirect', () => ({ hardRedirect: hardRedirectMock }));

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
    // 默认关闭 SSO：既有的密码登录用例不该因为多了一个入口而变化。
    ssoStatusMock.mockResolvedValue({ enabled: false, provider_name: '钉钉' });
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

  it('登录失败：不存令牌、不跳转（login 声明了 skipErrorHandler，提示由本屏自己呈现）', async () => {
    loginMock.mockRejectedValue(new Error('401'));
    renderLogin();
    await submit('admin', 'wrong');

    await waitFor(() => expect(loginMock).toHaveBeenCalled());
    expect(getToken()).toBeNull();
    expect(pushMock).not.toHaveBeenCalled();
  });

  /**
   * 回归 issue #58：后端不可达曾被显示成「用户名或密码错误」。两者要求用户做的动作
   * 相反，说错了他只会一遍遍重试密码。所以这三条守的是「失败类型不许被合并」。
   */
  describe('失败文案按类型分开', () => {
    it('401：说凭证错误', async () => {
      loginMock.mockRejectedValue({
        response: { status: 401, data: { message: '用户名或密码错误' } },
      });
      renderLogin();
      await submit('admin', 'wrong');

      expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument();
    });

    it('拿不到响应（后端不可达）：说连不上服务器，绝不说密码错', async () => {
      loginMock.mockRejectedValue(Object.assign(new Error('Network Error'), { request: {} }));
      renderLogin();
      await submit();

      expect(await screen.findByText('无法连接服务器，请检查网络后重试')).toBeInTheDocument();
      expect(screen.queryByText(/密码错误/)).not.toBeInTheDocument();
    });

    it('5xx 且响应体不是统一错误契约（如 nginx 502）：说服务不可用并带上状态码', async () => {
      loginMock.mockRejectedValue({
        response: { status: 502, data: '<html>Bad Gateway</html>' },
      });
      renderLogin();
      await submit();

      expect(await screen.findByText('服务暂时不可用，请稍后重试（502）')).toBeInTheDocument();
      expect(screen.queryByText(/密码错误/)).not.toBeInTheDocument();
    });
  });

  it('用户名密码为空时不发请求（表单必填校验）', async () => {
    renderLogin();
    await userEvent.setup().click(screen.getByRole('button', { name: /登 ?录/ }));

    await waitFor(() => expect(screen.getByText('请输入用户名')).toBeInTheDocument());
    expect(loginMock).not.toHaveBeenCalled();
  });

  describe('钉钉登录入口', () => {
    it('SSO 关闭时不显示钉钉按钮', async () => {
      ssoStatusMock.mockResolvedValue({ enabled: false, provider_name: '钉钉' });
      renderLogin();

      await waitFor(() => expect(ssoStatusMock).toHaveBeenCalled());
      expect(screen.queryByRole('button', { name: /钉钉/ })).not.toBeInTheDocument();
    });

    it('SSO 开启时显示钉钉按钮', async () => {
      ssoStatusMock.mockResolvedValue({ enabled: true, provider_name: '钉钉' });
      renderLogin();

      await waitFor(() =>
        expect(screen.getByRole('button', { name: /钉钉/ })).toBeInTheDocument(),
      );
    });

    it('点击钉钉登录：取授权地址并整页跳转过去', async () => {
      ssoStatusMock.mockResolvedValue({ enabled: true, provider_name: '钉钉' });
      ssoAuthorizeUrlMock.mockResolvedValue({ url: 'https://login.dingtalk.com/oauth2/auth?x=1' });
      renderLogin();

      const button = await screen.findByRole('button', { name: /钉钉/ });
      await userEvent.setup().click(button);

      await waitFor(() =>
        expect(hardRedirectMock).toHaveBeenCalledWith(
          'https://login.dingtalk.com/oauth2/auth?x=1',
        ),
      );
      // 授权地址是外域，必须整页跳转；用 history.push 只会在 SPA 内找不到这条路由。
      expect(pushMock).not.toHaveBeenCalled();
    });

    it('status 查询失败时按钮不出现，且不影响密码登录', async () => {
      ssoStatusMock.mockRejectedValue(new Error('boom'));
      renderLogin();

      await waitFor(() => expect(ssoStatusMock).toHaveBeenCalled());
      expect(screen.queryByRole('button', { name: /钉钉/ })).not.toBeInTheDocument();
      expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
    });

    /**
     * 默认登录方式是钉钉扫码，账号密码收进切换链接后面——不是并列展示。
     * SSO 关闭时没有这个切换态，密码表单本就是唯一入口，见上面「不显示钉钉按钮」那条。
     */
    it('SSO 开启时默认展示钉钉扫码登录，密码表单收起', async () => {
      ssoStatusMock.mockResolvedValue({ enabled: true, provider_name: '钉钉' });
      renderLogin();

      await screen.findByRole('button', { name: /钉钉/ });
      expect(screen.queryByPlaceholderText('用户名')).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText('密码')).not.toBeInTheDocument();
    });

    it('可以切换到账号密码登录，再切回钉钉扫码登录', async () => {
      ssoStatusMock.mockResolvedValue({ enabled: true, provider_name: '钉钉' });
      renderLogin();
      const user = userEvent.setup();

      await screen.findByRole('button', { name: /钉钉/ });
      await user.click(screen.getByText('使用账号密码登录'));

      expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /钉钉/ })).not.toBeInTheDocument();

      await user.click(screen.getByText('使用钉钉扫码登录'));

      expect(await screen.findByRole('button', { name: /钉钉/ })).toBeInTheDocument();
      expect(screen.queryByPlaceholderText('用户名')).not.toBeInTheDocument();
    });
  });
});
