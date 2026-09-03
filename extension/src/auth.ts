// 鉴权占位方案：运营手动从已登录的 KeywordIQ 网页复制一个 JWT，粘贴进 options
// page，存进 chrome.storage.local。这是刻意的临时方案——正式方案（读网页会话 /
// 独立 Personal Access Token / 插件内登录）是另一个尚未拍板的决定，本模块是
// 唯一需要在方案定下来后重写的地方（design.md 决策 1）。

import { MAX_REPORT_ITEMS } from './capture-accumulator';

const TOKEN_KEY = 'keywordiq_token';
const API_BASE_KEY = 'keywordiq_api_base';
const MAX_REPORT_ITEMS_KEY = 'keywordiq_max_report_items';

export async function getAuthToken(): Promise<string | null> {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  const value = result[TOKEN_KEY];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

export async function setAuthToken(token: string): Promise<void> {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}

export async function getApiBaseUrl(): Promise<string | null> {
  const result = await chrome.storage.local.get(API_BASE_KEY);
  const value = result[API_BASE_KEY];
  return typeof value === 'string' && value.length > 0 ? value.replace(/\/+$/, '') : null;
}

export async function setApiBaseUrl(baseUrl: string): Promise<void> {
  await chrome.storage.local.set({ [API_BASE_KEY]: baseUrl.replace(/\/+$/, '') });
}

/**
 * 单次上报的条目数上限。这是运行时配置（服务端 `COMPETITOR_MAX_REPORT_ITEMS`
 * 允许部署方设成任意正整数，默认才是 200），插件不能把默认值当协议常量写死
 * ——那样任何一侧改了配置，插件要么过早拒绝、要么继续攒到服务端实际上限之上
 * 才在提交时收到 422（PR #68 三次复审 P2）。这里选择让运营在 options page 里
 * 手填一份镜像值，与 API 地址、token 走同一条本地配置路径；插件没有能力在
 * 不新增后端端点的前提下自动读到服务端的真实配置（design.md 的既有非目标：
 * 本变更不改动任何后端代码）。
 */
export async function getMaxReportItems(): Promise<number> {
  const result = await chrome.storage.local.get(MAX_REPORT_ITEMS_KEY);
  const value = result[MAX_REPORT_ITEMS_KEY];
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : MAX_REPORT_ITEMS;
}

export async function setMaxReportItems(value: number): Promise<void> {
  await chrome.storage.local.set({ [MAX_REPORT_ITEMS_KEY]: value });
}
