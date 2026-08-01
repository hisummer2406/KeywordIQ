import { beforeEach, describe, expect, it, vi } from 'vitest';

const { pushMock, messageError, messageWarning, locationRef, getCurrentUserMock } = vi.hoisted(
  () => ({
    pushMock: vi.fn(),
    messageError: vi.fn(),
    messageWarning: vi.fn(),
    locationRef: { pathname: '/analysis' },
    getCurrentUserMock: vi.fn(),
  }),
);

vi.mock('@umijs/max', () => ({
  history: {
    push: pushMock,
    get location() {
      return locationRef;
    },
  },
}));
vi.mock('antd', () => ({ message: { error: messageError, warning: messageWarning } }));
vi.mock('@/services/auth', () => ({ getCurrentUser: getCurrentUserMock }));

import { getInitialState, layout, request } from '@/app';
import { clearToken, getToken, setToken } from '@/utils/token';

/**
 * errorHandler 是错误提示的唯一出口——页面层不写 try/catch 也必须让用户看见失败。
 * 这组用例守住的正是"统一出口"这个本变更的核心意图。
 */
describe('全局 errorHandler', () => {
  const handler = (request.errorConfig as any).errorHandler;

  const httpError = (status: number, data?: unknown, config?: unknown) => ({
    response: { status, data },
    config,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    locationRef.pathname = '/analysis';
  });

  it('按统一错误契约的 message 提示', () => {
    const err = httpError(409, {
      success: false,
      code: 'user.duplicate_name',
      message: '用户名已存在',
      request_id: 'r1',
    });

    expect(() => handler(err)).toThrow();
    expect(messageError).toHaveBeenCalledWith('用户名已存在');
  });

  it('校验错误附上首个出错字段，便于用户定位', () => {
    const err = httpError(422, {
      success: false,
      code: 'request.invalid',
      message: '请求参数不合法',
      request_id: 'r2',
      fields: [{ field: 'username', msg: 'Field required' }],
    });

    expect(() => handler(err)).toThrow();
    expect(messageError).toHaveBeenCalledWith('请求参数不合法：username Field required');
  });

  it('非登录页的 401 清 token 跳登录，且不再往下抛', () => {
    setToken('expired');
    handler(httpError(401, { message: '无效令牌' }));

    expect(getToken()).toBeNull();
    expect(messageWarning).toHaveBeenCalled();
    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('skipErrorHandler 不弹提示，但仍把错误抛给调用方', () => {
    const err = httpError(401, { message: '用户名或密码错误' }, { skipErrorHandler: true });
    locationRef.pathname = '/login';

    expect(() => handler(err)).toThrow();
    expect(messageError).not.toHaveBeenCalled();
    expect(messageWarning).not.toHaveBeenCalled();
  });

  it('无响应体时用兜底文案，不显示 undefined', () => {
    expect(() => handler(httpError(500, undefined))).toThrow();
    expect(messageError).toHaveBeenCalledWith('请求失败（500）');
  });

  it('网络不通时提示服务器无响应', () => {
    expect(() => handler({ request: {} })).toThrow();
    expect(messageError).toHaveBeenCalledWith('服务器无响应，请稍后重试');
  });
});

/**
 * 守卫判据必须与 access.ts 的权限判定同源（都看 currentUser）。曾试过改看 token 来
 * 绕开登录后的竞态，结果只是把"弹回登录页"换成了"落到 403"——守卫放行了，权限判定
 * 仍读 currentUser 而判成无权限。竞态的正解在跳转方（见 pages/Login）。
 */
describe('layout 路由守卫', () => {
  const onPageChangeWith = (initialState: unknown) =>
    (layout({ initialState } as any) as any).onPageChange;

  beforeEach(() => {
    vi.clearAllMocks();
    clearToken();
    locationRef.pathname = '/analysis';
  });

  it('未登录访问受保护页跳登录', () => {
    onPageChangeWith({})();

    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('已登录访问受保护页不跳转', () => {
    onPageChangeWith({ currentUser: { user_name: 'admin' } })();

    expect(pushMock).not.toHaveBeenCalled();
  });

  it('登录页自身不跳转，避免自跳循环', () => {
    locationRef.pathname = '/login';
    onPageChangeWith({})();

    expect(pushMock).not.toHaveBeenCalled();
  });
});

/**
 * 无效 token 必须就地清掉：留着它，每次进屏都会先拿它换一次 401，而用户既拿不到
 * currentUser、也没有任何东西把这张废令牌收走。
 */
describe('getInitialState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearToken();
  });

  it('token 无效导致拉取用户失败时清掉 token', async () => {
    setToken('stale-token');
    getCurrentUserMock.mockRejectedValueOnce(new Error('401'));

    const state = await getInitialState();

    expect(state.currentUser).toBeUndefined();
    expect(getToken()).toBeNull();
  });

  it('token 有效时带回当前用户，且保留 token', async () => {
    setToken('good-token');
    getCurrentUserMock.mockResolvedValueOnce({ user_name: 'admin' });

    const state = await getInitialState();

    expect(state.currentUser).toEqual({ user_name: 'admin' });
    expect(getToken()).toBe('good-token');
  });
});
