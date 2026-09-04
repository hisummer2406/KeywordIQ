// 鉴权：令牌从已登录的 KeywordIQ 网页会话同步而来（session-sync.ts），手动粘贴
// 仍作为兜底保留。所有取值都经由本模块——这正是 design.md 决策 1 当初把耦合点收在
// 一个函数上的目的：换鉴权方案不必碰 DOM 解析、请求组装与幂等逻辑。
//
// **仍未解决的三条**（要解决必须改后端，见本变更 design.md「明确不做」）：令牌仍是
// 24 小时期的网页 JWT；仍是全权限的；无法在不动该用户账号的前提下单独收回插件权限。

import { MAX_REPORT_ITEMS } from './capture-accumulator';

const TOKEN_KEY = 'keywordiq_token';
const TOKEN_SYNCED_AT_KEY = 'keywordiq_token_synced_at';
const API_BASE_KEY = 'keywordiq_api_base';
const WEB_ORIGIN_KEY = 'keywordiq_web_origin';
const MAX_REPORT_ITEMS_KEY = 'keywordiq_max_report_items';

export async function getAuthToken(): Promise<string | null> {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  const value = result[TOKEN_KEY];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

export async function setAuthToken(token: string): Promise<void> {
  await chrome.storage.local.set({
    [TOKEN_KEY]: token,
    [TOKEN_SYNCED_AT_KEY]: new Date().toISOString(),
  });
}

/** 令牌是什么时候拿到的。设置页据此回答"这个令牌是不是很久以前的了"。 */
export async function getTokenSyncedAt(): Promise<string | null> {
  const result = await chrome.storage.local.get(TOKEN_SYNCED_AT_KEY);
  const value = result[TOKEN_SYNCED_AT_KEY];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * 读取网页会话用的来源。**独立于 API 地址，不能复用**（本变更 design 决策 1）：
 * 本地开发下网页在 `:8001`、API 在 `:8000`，是两个来源；复用会去一个从未写入过
 * 令牌的来源读 localStorage，读到空——表现与"用户没登录"一模一样，而原因完全不同。
 *
 * 未配置时回退到 API 地址：生产经 nginx 同域托管 SPA 与 `/api`，那时两者确实相同。
 */
export async function getWebOrigin(): Promise<string | null> {
  const result = await chrome.storage.local.get(WEB_ORIGIN_KEY);
  const value = result[WEB_ORIGIN_KEY];
  if (typeof value === 'string' && value.length > 0) return value.replace(/\/+$/, '');
  return getApiBaseUrl();
}

export async function setWebOrigin(origin: string): Promise<void> {
  await chrome.storage.local.set({ [WEB_ORIGIN_KEY]: origin.replace(/\/+$/, '') });
}

/**
 * 清掉独立设置过的网页来源，回到"回退 API 地址"这条默认规则。
 *
 * **必须真的删掉这个 key，不能写入空字符串。** `getWebOrigin()` 判定"是否已独立
 * 配置"的依据是 `value.length > 0`——空字符串同样会落进这个分支导致误判，但更根本
 * 的问题是：不删的话，用户在设置页把网页地址输入框清空并保存，storage 里的旧值
 * 原样还在，`getWebOrigin()` 会继续返回那个用户已经明确要求删掉的地址，回退规则
 * 因此从未生效过。这正是"清空网页地址应当回退到 API 地址"这条契约的唯一实现点。
 */
export async function clearWebOrigin(): Promise<void> {
  await chrome.storage.local.remove(WEB_ORIGIN_KEY);
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
