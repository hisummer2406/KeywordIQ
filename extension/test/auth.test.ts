import { beforeEach, describe, expect, it } from 'vitest';
import {
  getApiBaseUrl,
  getAuthToken,
  getMaxReportItems,
  setApiBaseUrl,
  setAuthToken,
  setMaxReportItems,
} from '../src/auth';
import { MAX_REPORT_ITEMS } from '../src/capture-accumulator';
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

  it('未设置时 getMaxReportItems 返回默认值（与 capture-accumulator.ts 的 MAX_REPORT_ITEMS 同一个常量）', async () => {
    expect(await getMaxReportItems()).toBe(MAX_REPORT_ITEMS);
  });

  it('设置后能取回自定义上限（PR #68 三次复审 P2：上限是运行时配置，不是写死的协议常量）', async () => {
    await setMaxReportItems(100);
    expect(await getMaxReportItems()).toBe(100);
  });

  it('存储里的值非正整数时回退默认值', async () => {
    await chrome.storage.local.set({ keywordiq_max_report_items: -5 });
    expect(await getMaxReportItems()).toBe(MAX_REPORT_ITEMS);

    await chrome.storage.local.set({ keywordiq_max_report_items: 1.5 });
    expect(await getMaxReportItems()).toBe(MAX_REPORT_ITEMS);
  });
});
