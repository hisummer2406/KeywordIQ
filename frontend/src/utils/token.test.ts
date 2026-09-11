import { beforeEach, describe, expect, it } from 'vitest';

import { clearToken, getToken, isStaleAuthResponse, setToken, TOKEN_KEY } from '@/utils/token';

/** 构造一个可被 setToken/isStaleAuthResponse 解出 iat 的假 JWT——不需要真实签名，
 * 这两个函数只读 payload 段的 `iat`，不校验签名。 */
function fakeJwt(iat: number): string {
  return `header.${btoa(JSON.stringify({ iat }))}.sig`;
}

beforeEach(() => {
  clearToken();
});

describe('setToken', () => {
  it('本地没有令牌时直接写入', () => {
    setToken(fakeJwt(100));
    expect(getToken()).toBe(fakeJwt(100));
  });

  it('新令牌 iat 更新时正常覆盖', () => {
    setToken(fakeJwt(100));
    setToken(fakeJwt(200));
    expect(getToken()).toBe(fakeJwt(200));
  });

  it('新令牌 iat 更旧时跳过写入——同一账号并发登录、输家的响应后到时不覆盖赢家的令牌（issue #89 PR #91 复审第七轮）', () => {
    setToken(fakeJwt(200)); // 赢家的响应先到
    setToken(fakeJwt(100)); // 输家的响应后到，iat 更早

    expect(getToken()).toBe(fakeJwt(200));
  });

  it('两个 iat 相等时按"不更旧"处理，正常覆盖（不产生死锁式的互相拒绝）', () => {
    setToken(fakeJwt(150));
    setToken(fakeJwt(150));
    expect(getToken()).toBe(fakeJwt(150));
  });

  it('当前令牌解不出 iat（如测试常用的假字符串）时不做比较，直接覆盖', () => {
    localStorage.setItem(TOKEN_KEY, 'not-a-jwt-at-all');
    setToken(fakeJwt(1));
    expect(getToken()).toBe(fakeJwt(1));
  });

  it('新令牌解不出 iat 时不做比较，直接覆盖', () => {
    setToken(fakeJwt(999));
    setToken('also-not-a-jwt');
    expect(getToken()).toBe('also-not-a-jwt');
  });

  it('两者都不是合法 JWT 时保持此前"无条件覆盖"的行为（不影响其余测试文件里的假 token 用法）', () => {
    setToken('token-a');
    setToken('token-b');
    expect(getToken()).toBe('token-b');
  });
});

describe('isStaleAuthResponse', () => {
  it('本地没有令牌（已在别处退出）时不算陈旧响应——这是真实的会话结束，调用方必须正常清理（PR #91 复审第七轮）', () => {
    setToken('some-token');
    clearToken();

    expect(isStaleAuthResponse('some-token')).toBe(false);
  });

  it('当前令牌与请求发起时用的令牌相同时不算陈旧', () => {
    setToken('current');
    expect(isStaleAuthResponse('current')).toBe(false);
  });

  it('当前存在另一张不同的令牌时算陈旧（本浏览器重新登录过）', () => {
    setToken('fresh-after-relogin');
    expect(isStaleAuthResponse('stale-before-relogin')).toBe(true);
  });
});
