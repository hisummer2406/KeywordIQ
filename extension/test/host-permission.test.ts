import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ensureHostPermission, toOriginPattern } from '../src/host-permission';
import { installChromeStub } from './chrome-stub';

describe('toOriginPattern', () => {
  it('从 baseUrl 提取 scheme + host，末尾统一补 /*', () => {
    expect(toOriginPattern('http://localhost:8000')).toBe('http://localhost:8000/*');
  });

  it('生产域名同样只取 scheme + host，不带路径', () => {
    expect(toOriginPattern('https://api.keywordiq.example/some/path')).toBe(
      'https://api.keywordiq.example/*',
    );
  });

  it('不合法的 URL 抛出异常，调用方按地址格式错误处理', () => {
    expect(() => toOriginPattern('not-a-url')).toThrow();
  });
});

describe('ensureHostPermission', () => {
  beforeEach(() => {
    installChromeStub();
  });

  it('尚未授权时会调用 chrome.permissions.request 并返回其结果', async () => {
    const requestSpy = vi.spyOn(chrome.permissions, 'request');

    const granted = await ensureHostPermission('http://localhost:8000');

    expect(granted).toBe(true);
    expect(requestSpy).toHaveBeenCalledWith({ origins: ['http://localhost:8000/*'] });
  });

  it('已授权时直接返回 true，不重复弹出请求（chrome.permissions.request 不被调用）', async () => {
    await chrome.permissions.request({ origins: ['http://localhost:8000/*'] });
    const requestSpy = vi.spyOn(chrome.permissions, 'request');

    const granted = await ensureHostPermission('http://localhost:8000');

    expect(granted).toBe(true);
    expect(requestSpy).not.toHaveBeenCalled();
  });

  it('用户在 Chrome 权限弹窗里拒绝时返回 false', async () => {
    chrome.permissions.request = vi.fn().mockResolvedValue(false);

    const granted = await ensureHostPermission('https://api.example.com');

    expect(granted).toBe(false);
  });
});
