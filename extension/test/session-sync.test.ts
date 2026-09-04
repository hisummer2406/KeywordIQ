import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getAuthToken } from '../src/auth';
import { syncTokenFromWeb } from '../src/session-sync';
import { installChromeStub } from './chrome-stub';

const ORIGIN = 'http://localhost:8001';
const PATTERN = 'http://localhost:8001/*';

/** 让该来源处于"已授权"状态——正常路径的前置。 */
async function grant(): Promise<void> {
  await (globalThis as any).chrome.permissions.request({ origins: [PATTERN] });
}

/** 装一个返回指定标签页的 tabs.query。 */
function withTabs(tabs: { id?: number }[]): void {
  (globalThis as any).chrome.tabs.query = vi.fn(async () => tabs);
}

/** 装一个返回指定注入结果的 executeScript。 */
function withInjectionResult(result: unknown): void {
  (globalThis as any).chrome.scripting.executeScript = vi.fn(async () => [{ result }]);
}

describe('syncTokenFromWeb', () => {
  beforeEach(() => {
    installChromeStub();
  });

  it('未授权该来源时返回 no_permission，且不去查标签页', async () => {
    const query = vi.fn(async () => [{ id: 1 }]);
    (globalThis as any).chrome.tabs.query = query;

    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_permission' });
    // 没权限就不该继续往下走——查了也拿不到，白发一次 IPC
    expect(query).not.toHaveBeenCalled();
  });

  it('没有该来源的标签页时返回 no_tab', async () => {
    await grant();
    withTabs([]);
    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_tab' });
  });

  it('标签页存在但 localStorage 没有令牌时返回 no_token（"没开网页"与"没登录"必须分开）', async () => {
    await grant();
    withTabs([{ id: 7 }]);
    withInjectionResult(null);
    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_token' });
  });

  it('读到空串同样视作 no_token，不会把空串当令牌存下去', async () => {
    await grant();
    withTabs([{ id: 7 }]);
    withInjectionResult('');
    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_token' });
    expect(await getAuthToken()).toBeNull();
  });

  it('读到令牌时返回 ok 并存进本地存储', async () => {
    await grant();
    withTabs([{ id: 7 }]);
    withInjectionResult('abc.def.ghi');

    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'ok', token: 'abc.def.ghi' });
    expect(await getAuthToken()).toBe('abc.def.ghi');
  });

  it('注入本身抛错（标签页正在导航/已关闭）按 no_tab 处理，不把异常抛给调用方', async () => {
    await grant();
    withTabs([{ id: 7 }]);
    (globalThis as any).chrome.scripting.executeScript = vi.fn(async () => {
      throw new Error('Cannot access contents of the page');
    });
    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_tab' });
  });

  it('标签页没有 id 时视作没有可注入的标签页', async () => {
    await grant();
    withTabs([{}]);
    expect(await syncTokenFromWeb(ORIGIN)).toEqual({ outcome: 'no_tab' });
  });

  it('按来源生成 match pattern 去查标签页，而不是拿原始 URL 去比', async () => {
    await grant();
    const query = vi.fn(async () => [{ id: 7 }]);
    (globalThis as any).chrome.tabs.query = query;
    withInjectionResult('tok');

    await syncTokenFromWeb('http://localhost:8001/analysis?site=US');
    // 用户填的地址可能带路径与查询串；查询必须按来源展开成 `origin/*`，
    // 否则只有停在那一个确切 URL 上的标签页才会被找到
    expect(query).toHaveBeenCalledWith({ url: PATTERN });
  });
});
