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

  it('不调用 chrome.permissions.contains（PR #68 五次复审 P1：先 await contains 再决定要不要 request 会丢失 user gesture）', async () => {
    const containsSpy = vi.spyOn(chrome.permissions, 'contains');

    await ensureHostPermission('http://localhost:8000');

    expect(containsSpy).not.toHaveBeenCalled();
  });

  it('已授权的来源再次调用仍会 request（不做 contains 短路）；Chrome 保证已授权时 request 直接返回 true 不弹窗，这里用 stub 验证返回值语义', async () => {
    await chrome.permissions.request({ origins: ['http://localhost:8000/*'] });
    const requestSpy = vi.spyOn(chrome.permissions, 'request');

    const granted = await ensureHostPermission('http://localhost:8000');

    expect(granted).toBe(true);
    expect(requestSpy).toHaveBeenCalledWith({ origins: ['http://localhost:8000/*'] });
  });

  it('用户在 Chrome 权限弹窗里拒绝时返回 false', async () => {
    chrome.permissions.request = vi.fn().mockResolvedValue(false);

    const granted = await ensureHostPermission('https://api.example.com');

    expect(granted).toBe(false);
  });

  it('baseUrl 不是合法 URL 时同步抛出，不调用 chrome.permissions.request（不消耗 user gesture）', async () => {
    const requestSpy = vi.spyOn(chrome.permissions, 'request');

    await expect(ensureHostPermission('not-a-url')).rejects.toThrow();

    expect(requestSpy).not.toHaveBeenCalled();
  });
});
