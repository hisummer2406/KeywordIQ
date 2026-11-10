import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearWebOrigin,
  getApiBaseUrl,
  getAuthToken,
  getWebOrigin,
  setApiBaseUrl,
  setAuthToken,
  setWebOrigin,
} from '../src/auth';
import { installChromeStub } from './chrome-stub';

describe('auth', () => {
  beforeEach(() => {
    installChromeStub();
  });

  it('未设置时 getAuthToken 返回 null', async () => {
    expect(await getAuthToken()).toBeNull();
  });

  it('设置后能取回', async () => {
    await setAuthToken('abc.def.ghi');
    expect(await getAuthToken()).toBe('abc.def.ghi');
  });

  it('未设置时 getApiBaseUrl 返回 null', async () => {
    expect(await getApiBaseUrl()).toBeNull();
  });

  it('设置后能取回，且去掉结尾多余的斜杠', async () => {
    await setApiBaseUrl('http://localhost:8000/');
    expect(await getApiBaseUrl()).toBe('http://localhost:8000');
  });

  describe('getWebOrigin', () => {
    it('未单独设置且未设置 API 地址时返回 null', async () => {
      expect(await getWebOrigin()).toBeNull();
    });

    it('未单独设置时回退到 API 地址（design 决策 1：生产环境网页与 API 同域）', async () => {
      await setApiBaseUrl('http://localhost:8000');
      expect(await getWebOrigin()).toBe('http://localhost:8000');
    });

    it('单独设置后优先于 API 地址，且去掉结尾多余的斜杠', async () => {
      await setApiBaseUrl('http://localhost:8000');
      await setWebOrigin('http://localhost:8001/');
      expect(await getWebOrigin()).toBe('http://localhost:8001');
    });

    it('clearWebOrigin 之后重新回退到 API 地址（PR #73 审核意见 P1：清空必须真正删除，' +
      '不能只是不写入——否则用户清空网页地址输入框后，storage 里的旧值原样留着，' +
      '"留空回退 API 地址"这条契约就没有兑现）', async () => {
      await setApiBaseUrl('http://localhost:8000');
      await setWebOrigin('http://localhost:8001');
      expect(await getWebOrigin()).toBe('http://localhost:8001');

      await clearWebOrigin();
      expect(await getWebOrigin()).toBe('http://localhost:8000');
    });

    it('clearWebOrigin 在从未设置过时是空操作，不抛异常', async () => {
      await expect(clearWebOrigin()).resolves.toBeUndefined();
      expect(await getWebOrigin()).toBeNull();
    });
  });
});
