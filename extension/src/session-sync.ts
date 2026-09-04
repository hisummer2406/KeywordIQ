// 从已登录的 KeywordIQ 网页会话同步访问令牌。
//
// 网页把 JWT 存在 `localStorage` 的 `keywordiq_token`（前端 src/utils/token.ts 的
// TOKEN_KEY）。插件向那个来源的标签页注入一段读取脚本取回它——网页不需要做任何
// 配合，这是相对 `externally_connectable` 的取舍：后者要求前端硬编码扩展 ID，而
// 未打包的开发版扩展每次重载都可能换 ID（本变更 design 决策 2）。
//
// 键名与插件自己在 `chrome.storage.local` 里用的恰好同名，但两者是**完全独立的
// 存储**（网页 localStorage vs 扩展 storage），互不相通。

import { setAuthToken } from './auth';
import { toOriginPattern } from './host-permission';

/** 网页存放 JWT 的 localStorage 键，镜像前端 `src/utils/token.ts` 的 `TOKEN_KEY`。 */
const WEB_TOKEN_KEY = 'keywordiq_token';

/**
 * 同步的四种结局。**不能合并成"同步失败"**（design 决策 3）：三种失败要求运营做的
 * 事完全不同——重新授权 / 打开网页 / 在网页上登录。合并之后提示只能说一句放之四海
 * 皆准的废话，而运营无从知道该干什么。
 */
export type SyncOutcome = 'ok' | 'no_permission' | 'no_tab' | 'no_token';

export interface SyncResult {
  outcome: SyncOutcome;
  /** 成功时取到的令牌，供调用方判断"是不是换了一个新的"（401 重试的前置）。 */
  token?: string;
}

/**
 * 注入到网页里执行的函数。**必须是自包含的**——它被序列化后在页面上下文里运行，
 * 拿不到本模块的任何闭包变量，所以键名在这里重复写一遍而不是引用上面的常量。
 */
function readTokenFromPage(): string | null {
  try {
    return window.localStorage.getItem('keywordiq_token');
  } catch {
    // 隐私模式等存储不可读的情况：当作没有令牌，不让整条链路抛异常
    return null;
  }
}

/**
 * 从 `webOrigin` 的标签页里读取令牌并存下。
 *
 * 不主动开新标签页：那会在运营没有预期的时候弹出一个页面，而"没开网页"本身就是
 * 一个需要他知道的状态（否则他不会意识到插件依赖那个登录态）。返回 `no_tab` 让
 * 调用方去提示。
 */
export async function syncTokenFromWeb(webOrigin: string): Promise<SyncResult> {
  const pattern = toOriginPattern(webOrigin);

  const granted = await chrome.permissions.contains({ origins: [pattern] });
  if (!granted) return { outcome: 'no_permission' };

  const tabs = await chrome.tabs.query({ url: pattern });
  const target = tabs.find((t) => typeof t.id === 'number');
  if (!target || typeof target.id !== 'number') return { outcome: 'no_tab' };

  let results: chrome.scripting.InjectionResult<string | null>[];
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: target.id },
      func: readTokenFromPage,
    });
  } catch {
    // 注入失败（标签页正在导航、页面已关闭、受限页面等）：与"没有标签页"同类，
    // 运营的动作都是"打开/刷新 KeywordIQ 网页再试"
    return { outcome: 'no_tab' };
  }

  const token = results[0]?.result;
  if (typeof token !== 'string' || token.length === 0) return { outcome: 'no_token' };

  await setAuthToken(token);
  return { outcome: 'ok', token };
}
