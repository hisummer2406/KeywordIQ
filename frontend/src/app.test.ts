import { beforeEach, describe, expect, it, vi } from 'vitest';

const { pushMock, messageError, messageWarning, locationRef } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  messageError: vi.fn(),
  messageWarning: vi.fn(),
  locationRef: { pathname: '/analysis' },
}));

vi.mock('@umijs/max', () => ({
  history: {
    push: pushMock,
    get location() {
      return locationRef;
    },
  },
}));
vi.mock('antd', () => ({ message: { error: messageError, warning: messageWarning } }));

import { request } from '@/app';
import { setToken, getToken } from '@/utils/token';

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
