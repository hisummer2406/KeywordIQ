// service worker：唯一发起网络请求的地方（design.md 决策 6）。收到 content
// script 的采集结果后，组装请求体、发送并分类结果。

import { getApiBaseUrl, getAuthToken, getWebOrigin } from './auth';
import { getAutoConfig, nextDailyRunAt } from './auto-config';
import {
  ALARM_DAILY,
  isAutoTab,
  onRunAlarm,
  onTabComplete,
  onTabRemoved,
  resumeAfterStartup,
  startRun,
  stopRun,
  type RunnerDeps,
} from './auto-runner';
import { buildReportRequest } from './build-report';
import { fetchCollectorConfig } from './collector-config';
import type {
  AutoControlMessage,
  AutoControlResult,
  CaptureMessage,
  CaptureResult,
  ConfigMessage,
  ConfigResult,
} from './messages';
import { sendReport } from './report-client';
import { syncTokenFromWeb, type SyncOutcome, type SyncResult } from './session-sync';
import { fetchWatchedSellers } from './watch-client';

// 工具栏按钮点击 → 打开设置页。
//
// 采集本身不走这个按钮（那是页面右上角浮动面板的事），但按钮必须做点什么：
// 一个点了没有任何反应的图标，比没有图标更让人以为插件坏了。设置页是唯一
// 需要用户反复回去的地方（令牌 24 小时过期），指向它最合理。
chrome.action?.onClicked.addListener(() => {
  void chrome.runtime.openOptionsPage();
});

chrome.runtime.onMessage.addListener(
  (message: CaptureMessage | ConfigMessage | AutoControlMessage, sender, sendResponse) => {
    if (message?.type === 'CAPTURE_REPORT') {
      handleCapture(message).then(sendResponse);
      return true; // 告知 Chrome 这是异步响应，保持消息通道打开
    }
    if (message?.type === 'GET_COLLECTOR_CONFIG') {
      handleGetConfig().then(sendResponse);
      return true;
    }
    if (typeof message?.type === 'string' && message.type.startsWith('AUTO_')) {
      handleAutoControl(message as AutoControlMessage, sender?.tab?.id).then(sendResponse);
      return true;
    }
    return undefined;
  },
);

// ---------- 自动采集（试点，#205 阶段 1；本变更 design 决策 3） ----------

const runnerDeps: RunnerDeps = {
  now: () => new Date(),
  random: Math.random,
  getCap: () => handleGetConfig(),
  getSellers: async (limit) => {
    // getCap 已先行一步完成令牌自愈，这里直接读
    const token = await getAuthToken();
    const baseUrl = await getApiBaseUrl();
    if (!token || !baseUrl) return { kind: 'auth_error' };
    return fetchWatchedSellers(baseUrl, token, limit);
  },
  report: (message) => handleCapture(message),
  getSettings: async () => {
    const config = await getAutoConfig();
    return { pages: config.pages, maxStores: config.maxStores };
  },
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

/** 按设置创建或清除每日 alarm。开关关闭时不保留任何每日触发。 */
export async function ensureDailyAlarm(): Promise<void> {
  const config = await getAutoConfig();
  await chrome.alarms.clear(ALARM_DAILY);
  if (!config.enabled) return;
  await chrome.alarms.create(ALARM_DAILY, {
    when: nextDailyRunAt(config.dailyTime, new Date()),
    periodInMinutes: 24 * 60,
  });
}

export async function handleAutoControl(
  message: AutoControlMessage,
  tabId: number | undefined,
): Promise<AutoControlResult> {
  switch (message.type) {
    case 'AUTO_IS_AUTO_TAB':
      return { ok: true, isAutoTab: await isAutoTab(tabId) };
    case 'AUTO_RUN_NOW': {
      const outcome = await startRun('manual', runnerDeps);
      return outcome.started ? { ok: true } : { ok: false, reason: outcome.reason };
    }
    case 'AUTO_STOP':
      return { ok: await stopRun(runnerDeps) };
    case 'AUTO_CONFIG_CHANGED':
      await ensureDailyAlarm();
      return { ok: true };
  }
}

chrome.alarms?.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_DAILY) {
    void (async () => {
      const config = await getAutoConfig();
      if (config.enabled) await startRun('daily', runnerDeps);
    })();
    return;
  }
  void onRunAlarm(alarm.name, runnerDeps);
});

chrome.tabs?.onUpdated?.addListener((tabId, changeInfo) => {
  if (changeInfo.status === 'complete') void onTabComplete(tabId, runnerDeps);
});

chrome.tabs?.onRemoved?.addListener((tabId) => {
  void onTabRemoved(tabId, runnerDeps);
});

// alarm 不保证跨浏览器重启保留：启动和安装/升级时都重建每日 alarm，并接着做未完成的一轮
chrome.runtime.onStartup?.addListener(() => {
  void ensureDailyAlarm();
  void resumeAfterStartup(runnerDeps);
});

chrome.runtime.onInstalled?.addListener(() => {
  void ensureDailyAlarm();
});

/**
 * 读取服务端单批上限。令牌缺失或过期时与上报同一套自愈（从网页会话同步、至多重试一次）；
 * 任何一步失败都给 `unavailable`，**不回落到本地默认值**（#166 D4）。
 */
export async function handleGetConfig(): Promise<ConfigResult> {
  let token = await getAuthToken();
  if (!token) {
    const synced = await trySync();
    if (synced.outcome !== 'ok' || !synced.token) return { outcome: 'unavailable' };
    token = synced.token;
  }
  const baseUrl = await getApiBaseUrl();
  if (!baseUrl) return { outcome: 'unavailable' };

  let result = await fetchCollectorConfig(baseUrl, token);
  if (result.kind === 'auth_error') {
    const synced = await trySync();
    if (synced.outcome === 'ok' && synced.token && synced.token !== token) {
      result = await fetchCollectorConfig(baseUrl, synced.token);
    }
  }
  return result.kind === 'ok'
    ? { outcome: 'ok', maxReportItems: result.maxReportItems }
    : { outcome: 'unavailable' };
}

/** 同步失败的三种成因 → 各自的结局，不合并（messages.ts 有理由）。 */
function syncFailureResult(outcome: SyncOutcome): CaptureResult {
  switch (outcome) {
    case 'no_permission':
      return { outcome: 'sync_no_permission' };
    case 'no_tab':
      return { outcome: 'sync_no_tab' };
    case 'no_token':
      return { outcome: 'sync_not_logged_in' };
    case 'ok':
      return { outcome: 'missing_token' }; // 不可达，只是穷尽联合类型
  }
}

/** 从网页会话同步一次令牌。网页来源未配置或非法时视同"没有标签页"。 */
async function trySync(): Promise<SyncResult> {
  const webOrigin = await getWebOrigin();
  if (!webOrigin) return { outcome: 'no_tab' };
  try {
    return await syncTokenFromWeb(webOrigin);
  } catch {
    // 来源不是合法 URL（toOriginPattern 内部 new URL 抛错）等：按"没有标签页"处理，
    // 运营的下一步动作（去设置页确认网页地址、或打开网页）本来就是同一个
    return { outcome: 'no_tab' };
  }
}

export async function handleCapture(message: CaptureMessage): Promise<CaptureResult> {
  // 没有令牌就先从网页会话取一次——这是本变更的主路径：运营在网页上登录过，插件
  // 就该自己拿到，不必他去开发者工具里翻 localStorage 再粘过来。
  let token = await getAuthToken();
  if (!token) {
    const synced = await trySync();
    if (synced.outcome !== 'ok' || !synced.token) return syncFailureResult(synced.outcome);
    token = synced.token;
  }

  const baseUrl = await getApiBaseUrl();
  if (!baseUrl) return { outcome: 'missing_base_url' };

  // requestId/capturedAt 由 content script 冻结后随消息传入（decision 3 +
  // capture-accumulator.ts 的 freezeRequest），background 不再自己生成——它是
  // 无状态的 service worker，每次消息都当作独立事件处理，没有能力判断"这是
  // 同一批条目的重新提交"（PR #68 二次复审 P1）。sendReport 内部的所有自动
  // 重试都复用这同一份序列化结果。
  const body = buildReportRequest({
    sellerId: message.sellerId,
    site: message.site,
    items: message.items,
    requestId: message.requestId,
    capturedAt: message.capturedAt,
    pageContext: message.pageContext,
    sellerName: message.sellerName,
  });

  let outcome = await sendReport(baseUrl, token, body);

  // 401 自愈：网页会话往往还有效（运营刚在网页上用过），而插件手里这个已经过期。
  // 同步一次再重试**至多一次**，三条约束都不能松（本变更 design 决策 4）：
  //
  // - **只有拿到「不同的」令牌才重试**。网页那边也过期时同步回来的是同一串，
  //   再打一次纯属浪费一个请求，且会让运营多等一轮。
  // - **重发的是同一份 `body`**，`request_id` 与 `captured_at` 都是 content script
  //   冻结的那对。服务端按幂等键处理，所以这次重试**不会**产生第二条观测记录——
  //   若在这里重新生成幂等键，一次真实观测就会被记成两次。
  // - **至多一次**。401 除了"过期"还可能是"令牌本身无效"，那种重试多少次都一样。
  if (outcome.kind === 'auth_error') {
    const synced = await trySync();
    if (synced.outcome === 'ok' && synced.token && synced.token !== token) {
      outcome = await sendReport(baseUrl, synced.token, body);
    }
  }

  switch (outcome.kind) {
    case 'success':
      return {
        outcome: 'success',
        accepted: outcome.response.accepted,
        newAsins: outcome.response.new_asins,
        baselineEstablished: outcome.response.baseline_established,
        duplicate: outcome.response.duplicate_of !== null,
      };
    case 'auth_error':
      return { outcome: 'auth_error' };
    case 'rejected':
      return { outcome: 'rejected', message: outcome.message };
    case 'idempotency_conflict':
      return { outcome: 'idempotency_conflict', message: outcome.message };
    case 'network_error':
      return { outcome: 'network_error' };
  }
}
