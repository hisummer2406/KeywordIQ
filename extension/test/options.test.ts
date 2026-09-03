// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installChromeStub } from './chrome-stub';

function renderOptionsDom(): void {
  document.body.innerHTML = `
    <input id="api-base" type="text" />
    <input id="token" type="password" />
    <input id="max-report-items" type="number" />
    <button id="save"></button>
    <p id="status"></p>
  `;
}

// options.ts 的 init() 与 click 回调都是 `async () => {...}` 里链了好几个
// await（storage 读写、host permission 申请），链式微任务的跳数比 chrome.storage
// 的内存 stub 直觉上更多。用一次 setTimeout(0) 落到宏任务边界，比数 Promise.resolve()
// 的次数更稳。
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function loadOptionsModule(): Promise<void> {
  await import('../src/options');
  await flush();
}

describe('options page', () => {
  beforeEach(() => {
    installChromeStub();
    renderOptionsDom();
    vi.resetModules();
  });

  it('保存前会申请 API 地址对应的 host permission（PR #68 四次复审 P1）', async () => {
    const requestSpy = vi.spyOn(chrome.permissions, 'request');
    await loadOptionsModule();

    (document.getElementById('api-base') as HTMLInputElement).value = 'http://localhost:8000';
    (document.getElementById('token') as HTMLInputElement).value = 'tok';
    (document.getElementById('max-report-items') as HTMLInputElement).value = '200';
    document.getElementById('save')!.dispatchEvent(new Event('click'));
    await flush();

    expect(requestSpy).toHaveBeenCalledWith({ origins: ['http://localhost:8000/*'] });
    expect(document.getElementById('status')!.textContent).toBe('已保存');
  });

  it('权限被拒绝时不保存任何字段，并提示原因', async () => {
    chrome.permissions.request = vi.fn().mockResolvedValue(false);
    await loadOptionsModule();

    (document.getElementById('api-base') as HTMLInputElement).value = 'http://localhost:8000';
    (document.getElementById('token') as HTMLInputElement).value = 'tok';
    (document.getElementById('max-report-items') as HTMLInputElement).value = '200';
    document.getElementById('save')!.dispatchEvent(new Event('click'));
    await flush();

    const { getApiBaseUrl, getAuthToken } = await import('../src/auth');
    expect(await getApiBaseUrl()).toBeNull();
    expect(await getAuthToken()).toBeNull();
    expect(document.getElementById('status')!.textContent).toContain('未授予该地址的访问权限');
  });

  it('API 地址不是合法 URL 时提示格式错误，不申请权限也不保存', async () => {
    const requestSpy = vi.spyOn(chrome.permissions, 'request');
    await loadOptionsModule();

    (document.getElementById('api-base') as HTMLInputElement).value = 'not-a-url';
    (document.getElementById('token') as HTMLInputElement).value = 'tok';
    (document.getElementById('max-report-items') as HTMLInputElement).value = '200';
    document.getElementById('save')!.dispatchEvent(new Event('click'));
    await flush();

    expect(requestSpy).not.toHaveBeenCalled();
    expect(document.getElementById('status')!.textContent).toBe('API 地址不是合法的 URL');

    const { getApiBaseUrl } = await import('../src/auth');
    expect(await getApiBaseUrl()).toBeNull();
  });

  it('已授权过的地址再次保存仍会调用 request 但保存照常成功（PR #68 五次复审 P1：不做 contains 短路，Chrome 保证已授权时 request 直接返回 true 不弹窗）', async () => {
    await chrome.permissions.request({ origins: ['http://localhost:8000/*'] });
    const requestSpy = vi.spyOn(chrome.permissions, 'request');
    const containsSpy = vi.spyOn(chrome.permissions, 'contains');
    await loadOptionsModule();

    (document.getElementById('api-base') as HTMLInputElement).value = 'http://localhost:8000';
    (document.getElementById('token') as HTMLInputElement).value = 'tok';
    (document.getElementById('max-report-items') as HTMLInputElement).value = '200';
    document.getElementById('save')!.dispatchEvent(new Event('click'));
    await flush();

    expect(requestSpy).toHaveBeenCalledWith({ origins: ['http://localhost:8000/*'] });
    expect(containsSpy).not.toHaveBeenCalled();
    expect(document.getElementById('status')!.textContent).toBe('已保存');
  });
});
