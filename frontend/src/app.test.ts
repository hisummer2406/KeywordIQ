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

  it('会话被顶替时呈现区别于普通过期的明确原因（issue #89）', () => {
    setToken('kicked');
    handler(
      httpError(
        401,
        { success: false, code: 'auth.session_invalid', message: '登录状态已失效', request_id: 'r3' },
        { headers: { Authorization: 'Bearer kicked' } },
      ),
    );

    expect(getToken()).toBeNull();
    expect(messageWarning).toHaveBeenCalledWith('账号已在其他浏览器登录');
    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('迟到的旧请求 401 不清理已经生效的新登录状态（issue #89）', () => {
    // 这张令牌发起请求之后，本浏览器已经通过重新登录换成了另一张——响应此刻才姗姗来迟
    setToken('fresh-after-relogin');
    handler(
      httpError(
        401,
        { success: false, code: 'auth.session_invalid', message: '登录状态已失效', request_id: 'r4' },
        { headers: { Authorization: 'Bearer stale-before-relogin' } },
      ),
    );

    expect(getToken()).toBe('fresh-after-relogin');
    expect(messageWarning).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it('迟到的旧请求 401 到达时本地令牌已被清空（另一标签页主动退出），不是陈旧响应，仍按正常会话结束处理（PR #91 复审第七轮）', () => {
    // 请求发起时用的是 old-token；响应落地前，另一个标签页已经主动退出，本地已无令牌
    // ——这不是"本浏览器又登录了一次"，误判成陈旧响应会让这个标签页卡在一份服务端
    // 已经不存在的登录状态里，没有任何 storage 事件监听会替它清理。
    clearToken();
    handler(
      httpError(
        401,
        { success: false, code: 'auth.unauthorized', message: '无效令牌', request_id: 'r6' },
        { headers: { Authorization: 'Bearer old-token' } },
      ),
    );

    expect(getToken()).toBeNull();
    expect(messageWarning).toHaveBeenCalledWith('登录已过期，请重新登录');
    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('重新登录成功、尚未跳离登录页时，迟到的旧请求 401 同样被忽略（PR #91 审核 P2）', () => {
    // 重新登录已经拿到新 token，但 useLoginRedirect 还没来得及把路由从 /login 推走
    // ——这段窗口期里，一个用旧 token 发出的请求才姗姗来迟地返回 401。
    locationRef.pathname = '/login';
    setToken('fresh-after-relogin');
    handler(
      httpError(
        401,
        { success: false, code: 'auth.session_invalid', message: '登录状态已失效', request_id: 'r5' },
        { headers: { Authorization: 'Bearer stale-before-relogin' } },
      ),
    );

    expect(getToken()).toBe('fresh-after-relogin');
    expect(messageWarning).not.toHaveBeenCalled();
    expect(messageError).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
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

  it('/me 请求发起后本浏览器已重新登录，姗姗来迟的失败不清理新令牌，且用新令牌重新拉一次用户（PR #91 复审第五轮 P2）', async () => {
    // umi 的 request 包装无论 errorHandler 是否再抛都会让原 Promise 照常 reject，
    // 因此这里必须独立重复一次"陈旧响应不动新令牌"的判断，不能指望 errorHandler
    // 那边"忽略"了就万事大吉。
    //
    // 只是不清令牌还不够：若就此返回 undefined，这个标签页的 currentUser 会一直空着，
    // onPageChange 据此把它弹回登录页，而它其实已经拿到一张有效令牌——必须用这张新
    // 令牌重新拉一次，状态才能追上本地实际持有的会话。
    setToken('stale-before-relogin');
    getCurrentUserMock
      .mockImplementationOnce(async () => {
        // /me 发出之后、响应落地之前，本浏览器已经完成了一次新登录
        setToken('fresh-after-relogin');
        throw new Error('401');
      })
      .mockResolvedValueOnce({ username: 'admin' } as API.CurrentUser);

    const state = await getInitialState();

    expect(state.currentUser).toEqual({ username: 'admin' });
    expect(getToken()).toBe('fresh-after-relogin');
    expect(getCurrentUserMock).toHaveBeenCalledTimes(2);
  });

  it('重新拉取仍用的是失效令牌时，照常清理，不会死循环（PR #91 复审第五轮 P2）', async () => {
    // 第一次失败触发"令牌已变化，重新拉一次"；重试时令牌未再变化且依旧失败，
    // 必须落回"这张令牌本身就不作数"的既有分支，而不是无限重试。
    setToken('stale-before-relogin');
    getCurrentUserMock
      .mockImplementationOnce(async () => {
        setToken('also-invalid');
        throw new Error('401');
      })
      .mockRejectedValueOnce(new Error('401'));

    const state = await getInitialState();

    expect(state.currentUser).toBeUndefined();
    expect(getToken()).toBeNull();
    expect(getCurrentUserMock).toHaveBeenCalledTimes(2);
  });

  it('token 有效时带回当前用户，且保留 token', async () => {
    setToken('good-token');
    getCurrentUserMock.mockResolvedValueOnce({ user_name: 'admin' });

    const state = await getInitialState();

    expect(state.currentUser).toEqual({ user_name: 'admin' });
    expect(getToken()).toBe('good-token');
  });
});
