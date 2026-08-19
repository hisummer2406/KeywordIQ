import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { App } from 'antd';

const { pushMock, refreshMock, ssoCallbackMock, historyState, modelStore } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  refreshMock: vi.fn(),
  ssoCallbackMock: vi.fn(),
  historyState: { search: '' },
  modelStore: { currentUser: undefined as unknown, listeners: new Set<() => void>() },
}));

vi.mock('@umijs/max', async () => {
  const React = await vi.importActual<typeof import('react')>('react');
  return {
    history: {
      push: pushMock,
      get location() {
        return { search: historyState.search, pathname: '/sso/callback' };
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
vi.mock('@/services/auth', () => ({ ssoCallback: ssoCallbackMock }));

import SsoCallbackPage from '@/pages/SsoCallback';
import { clearToken, getToken } from '@/utils/token';

const renderPage = () =>
  render(
    <App>
      <SsoCallbackPage />
    </App>,
  );

/**
 * 钉钉扫码回调屏。
 *
 * 这一屏只有三条路径，但每条都必须**明确**：用户此刻刚从钉钉跳回来，页面上什么都不说
 * 等同于把他丢在半路——他既不知道该等还是该重试，也找不到回登录页的入口。
 */
describe('钉钉回调屏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearToken();
    historyState.search = '';
    modelStore.currentUser = undefined;
    modelStore.listeners.clear();
    ssoCallbackMock.mockResolvedValue({ token: 'jwt-sso' });
    refreshMock.mockImplementation(async () => {
      modelStore.currentUser = { user_name: 'zhangsan' };
      modelStore.listeners.forEach((notify) => notify());
    });
  });

  it('拿到 authCode：换令牌 → 刷新 initialState → 跳分析屏', async () => {
    historyState.search = '?authCode=ac-1&state=st-1';
    renderPage();

    await waitFor(() =>
      expect(ssoCallbackMock).toHaveBeenCalledWith({ auth_code: 'ac-1', state: 'st-1' }),
    );
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/analysis'));
    expect(getToken()).toBe('jwt-sso');
  });

  it('initialState 尚未带回 currentUser 时不跳转', async () => {
    // 与登录屏同一条竞态：refresh() 返回只说明刷新发起了，不代表 React 已提交新状态。
    // 抢跑会被 ProLayout 的守卫判成未登录推回登录页，或被 access 判成无权限渲染 403。
    historyState.search = '?authCode=ac-2&state=st-2';
    let commit = () => {};
    refreshMock.mockImplementation(async () => {
      commit = () => {
        modelStore.currentUser = { user_name: 'zhangsan' };
        modelStore.listeners.forEach((notify) => notify());
      };
    });

    renderPage();
    await waitFor(() => expect(ssoCallbackMock).toHaveBeenCalled());
    expect(pushMock).not.toHaveBeenCalled();

    commit();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/analysis'));
  });

  it('钉钉回传 error：不换令牌，给出回登录页的入口', async () => {
    historyState.search = '?error=access_denied&state=st-3';
    renderPage();

    await waitFor(() => expect(screen.getByRole('button', { name: /返回登录/ })).toBeInTheDocument());
    expect(ssoCallbackMock).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it('参数都没有（直接敲地址）：回登录页，不发请求', async () => {
    historyState.search = '';
    renderPage();

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith('/login'));
    expect(ssoCallbackMock).not.toHaveBeenCalled();
  });

  it('换令牌失败：不存令牌、不跳转，给出回登录页的入口', async () => {
    historyState.search = '?authCode=ac-bad&state=st-4';
    ssoCallbackMock.mockRejectedValue(new Error('400'));
    renderPage();

    await waitFor(() => expect(screen.getByRole('button', { name: /返回登录/ })).toBeInTheDocument());
    expect(getToken()).toBeNull();
    expect(pushMock).not.toHaveBeenCalledWith('/analysis');
  });
});
