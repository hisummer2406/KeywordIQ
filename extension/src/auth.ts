// 鉴权占位方案：运营手动从已登录的 KeywordIQ 网页复制一个 JWT，粘贴进 options
// page，存进 chrome.storage.local。这是刻意的临时方案——正式方案（读网页会话 /
// 独立 Personal Access Token / 插件内登录）是另一个尚未拍板的决定，本模块是
// 唯一需要在方案定下来后重写的地方（design.md 决策 1）。

const TOKEN_KEY = 'keywordiq_token';
const API_BASE_KEY = 'keywordiq_api_base';

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
