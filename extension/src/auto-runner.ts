// 自动采集（试点，#205 阶段 1）的后台状态机（design 决策 3）。
//
// MV3 的 Service Worker 随时可能被回收，所以这里不在内存里长时间 await：每一步都由事件
// 推动（chrome.alarms、tabs.onUpdated、tabs.onRemoved），运行状态每次变化后立即写入
// chrome.storage.local。事件处理统一经过 `serial()` 串行化，避免两个事件交错推进同一轮。

import { pickStoreGapMs, PAGE_TIMEOUT_MS, buildStorefrontUrl, decideAfterPage, type Coverage, type PageScan } from './auto-plan';
import { freezeRequest } from './capture-accumulator';
import type { AutoScanRequest, CaptureMessage, CaptureResult, ConfigResult } from './messages';
import type { SelectorOverrides } from './selector-overrides';
import type { ParsedItem } from './parse-storefront';
import type { WatchListOutcome, WatchedSeller } from './watch-client';

export const ALARM_DAILY = 'kiq-auto-daily';
export const ALARM_NEXT_STORE = 'kiq-auto-next-store';
export const ALARM_TIMEOUT = 'kiq-auto-timeout';
/**
 * 一轮运行期间每分钟触发一次的看门狗：保证「运行中」的状态始终有唤醒源。保存状态与创建
 * 下一步 alarm 之间被回收时，否则没有任何事件能再推进这一轮（PR #206 Codex P2）。
 */
export const ALARM_WATCHDOG = 'kiq-auto-watchdog';

const STATE_KEY = 'keywordiq_auto_run_state_v1';
const HISTORY_KEY = 'keywordiq_auto_run_history_v1';
const MAX_HISTORY = 30;
const MAX_REPORT_ATTEMPTS = 3;
const SCAN_RETRY_DELAYS_MS = [1500, 3000];

export type RunTrigger = 'daily' | 'manual';

export type RunStopReason =
  | 'blocked' // 验证码或访问拒绝
  | 'wrong_delivery' // 页头配送位置与目标邮编确认不符（stopDetail 记录页面上显示的文字）
  | 'delivery_unconfirmed' // 读不到配送位置或无法确认是目标邮编（stopDetail 记录能读到的文字）
  | 'page_timeout' // 页面超时或 content script 无响应，按访问受限处理
  | 'auth' // 令牌缺失、过期或同步失败
  | 'config_unavailable' // 读不到服务端单批上限
  | 'watch_list_unavailable' // 读不到关注清单
  | 'manual_stop';

export type StoreFailReason =
  | 'empty_page'
  | 'missing_page' // 前一页提示有下一页，后页却没有有效 ASIN：不提交残缺批次
  | 'over_cap'
  | 'bad_site'
  | 'tab_error'
  | 'report_rejected'
  | 'report_network'
  | 'report_conflict';

export interface StoreResult {
  sellerId: string;
  site: string;
  /** stopped：整轮在这家店停止（拦截、配送位置不符或未确认），这家店不上报。 */
  outcome: 'success' | 'failed' | 'stopped';
  reason?: StoreFailReason | RunStopReason;
  message?: string;
  pages: number;
  /** 合并去重后的有效 ASIN 数。 */
  items: number;
  /** 成功上报时的覆盖情况：complete 自然到达末页；limited 达到 N 页上限但页面仍有下一页。 */
  coverage?: Coverage;
  newAsins?: number;
  baselineEstablished?: boolean;
  duplicate?: boolean;
  startedAt: string;
  finishedAt: string;
}

interface CurrentStore {
  tabId?: number;
  page: number;
  /** 已经处理过解析结果的页码，防止同一页的重复 `complete` 事件被处理两次。 */
  scannedPage: number;
  /** 上一次被处理的解析结果来自哪个文档；同一文档再次回答说明新页面还没加载，忽略。 */
  lastDocId?: string;
  items: ParsedItem[];
  sellerName?: string;
  phase: 'collecting' | 'reporting';
  /** 进入上报阶段时确定，恢复后重发同一请求时沿用。 */
  coverage?: Coverage;
  requestId?: string;
  capturedAt?: string;
  reportAttempts: number;
  startedAt: string;
}

export interface RunState {
  runId: string;
  trigger: RunTrigger;
  status: 'running' | 'finished' | 'stopped';
  stopReason?: RunStopReason;
  stopDetail?: string;
  startedAt: string;
  finishedAt?: string;
  pages: number;
  cap: number;
  queue: WatchedSeller[];
  index: number;
  current?: CurrentStore;
  results: StoreResult[];
  /** 本轮最近一次读到的页头配送地址，用于事后核对采集时的配送上下文。 */
  deliveryText?: string;
  /** 开跑时的各站点目标邮编快照（如 { US: '10001' }）。 */
  deliveryTargets?: Record<string, string>;
  /** 最近一次检查配送位置的站点。 */
  deliverySite?: string;
  /** 开跑时快照的服务端选择器覆盖及版本；本轮一直用这份，恢复时沿用（extension-remote-selectors）。 */
  selectors?: SelectorOverrides;
  selectorsVersion?: string;
}

export interface RunnerDeps {
  now: () => Date;
  random: () => number;
  getCap: () => Promise<ConfigResult>;
  getSellers: (limit: number) => Promise<WatchListOutcome>;
  report: (message: CaptureMessage) => Promise<CaptureResult>;
  getSettings: () => Promise<{ pages: number; maxStores: number; deliveryTargets: Record<string, string> }>;
  sleep: (ms: number) => Promise<void>;
}

// ---------- 存储 ----------

export async function loadRunState(): Promise<RunState | null> {
  const stored = await chrome.storage.local.get(STATE_KEY);
  const value = stored[STATE_KEY];
  return value && typeof value === 'object' ? (value as RunState) : null;
}

async function saveRunState(state: RunState): Promise<void> {
  await chrome.storage.local.set({ [STATE_KEY]: state });
}

export async function loadRunHistory(): Promise<RunState[]> {
  const stored = await chrome.storage.local.get(HISTORY_KEY);
  const value = stored[HISTORY_KEY];
  return Array.isArray(value) ? (value as RunState[]) : [];
}

async function appendHistory(run: RunState): Promise<void> {
  const history = await loadRunHistory();
  const next = [...history.filter((r) => r.runId !== run.runId), run].slice(-MAX_HISTORY);
  await chrome.storage.local.set({ [HISTORY_KEY]: next });
}

// ---------- 串行化 ----------

let chain: Promise<unknown> = Promise.resolve();

/** 让事件处理一个接一个执行：Service Worker 里的异步回调否则会交错推进同一轮。 */
export function serial<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch(() => undefined);
  return run;
}

// ---------- 小工具 ----------

async function closeTab(tabId: number | undefined): Promise<void> {
  if (tabId === undefined) return;
  try {
    await chrome.tabs.remove(tabId);
  } catch {
    // 标签页已经不存在（运营手动关了、Chrome 重启）：不影响结果
  }
}

async function clearRunAlarms(): Promise<void> {
  await chrome.alarms.clear(ALARM_NEXT_STORE);
  await chrome.alarms.clear(ALARM_TIMEOUT);
  await chrome.alarms.clear(ALARM_WATCHDOG);
}

function iso(deps: RunnerDeps): string {
  return deps.now().toISOString();
}

async function endRun(
  state: RunState,
  deps: RunnerDeps,
  status: 'finished' | 'stopped',
  reason?: RunStopReason,
  detail?: string,
): Promise<RunState> {
  await clearRunAlarms();
  await closeTab(state.current?.tabId);
  const ended: RunState = {
    ...state,
    status,
    stopReason: reason,
    stopDetail: detail,
    finishedAt: iso(deps),
    current: undefined,
  };
  await saveRunState(ended);
  await appendHistory(ended);
  return ended;
}

function recordStore(state: RunState, deps: RunnerDeps, result: Omit<StoreResult, 'sellerId' | 'site' | 'startedAt' | 'finishedAt'>): void {
  const seller = state.queue[state.index];
  if (!seller) return;
  state.results.push({
    sellerId: seller.sellerId,
    site: seller.site,
    startedAt: state.current?.startedAt ?? iso(deps),
    finishedAt: iso(deps),
    ...result,
  });
}

/** 这家店处理完，指向下一家；有下一家就用 alarm 等待 30–60 秒，否则结束本轮。 */
async function advance(state: RunState, deps: RunnerDeps): Promise<RunState> {
  await chrome.alarms.clear(ALARM_TIMEOUT);
  await closeTab(state.current?.tabId);
  state.current = undefined;
  state.index += 1;
  if (state.index >= state.queue.length) return endRun(state, deps, 'finished');
  await saveRunState(state);
  await chrome.alarms.create(ALARM_NEXT_STORE, { when: deps.now().getTime() + pickStoreGapMs(deps.random) });
  return state;
}

async function armPageTimeout(deps: RunnerDeps): Promise<void> {
  await chrome.alarms.create(ALARM_TIMEOUT, { when: deps.now().getTime() + PAGE_TIMEOUT_MS });
}

// ---------- 状态机步骤 ----------

/** 打开当前这家店的第 1 页。站点未登记就记失败并跳到下一家（不等待）。 */
async function openCurrentStore(state: RunState, deps: RunnerDeps): Promise<RunState> {
  while (state.index < state.queue.length) {
    const seller = state.queue[state.index]!;
    state.current = {
      page: 1,
      scannedPage: 0,
      items: [],
      phase: 'collecting',
      reportAttempts: 0,
      startedAt: iso(deps),
    };
    const url = buildStorefrontUrl(seller.sellerId, seller.site, 1);
    if (!url) {
      recordStore(state, deps, { outcome: 'failed', reason: 'bad_site', pages: 0, items: 0 });
      state.current = undefined;
      state.index += 1;
      continue;
    }
    try {
      const tab = await chrome.tabs.create({ url, active: false });
      state.current.tabId = tab.id;
    } catch {
      recordStore(state, deps, { outcome: 'failed', reason: 'tab_error', pages: 0, items: 0 });
      state.current = undefined;
      state.index += 1;
      continue;
    }
    await saveRunState(state);
    await armPageTimeout(deps);
    return state;
  }
  return endRun(state, deps, 'finished');
}

async function reportCurrent(state: RunState, deps: RunnerDeps): Promise<RunState> {
  const seller = state.queue[state.index];
  const current = state.current;
  if (!seller || !current) return state;

  if (!current.requestId || !current.capturedAt) {
    const frozen = freezeRequest({ requestId: null, capturedAt: null });
    current.requestId = frozen.requestId;
    current.capturedAt = frozen.capturedAt;
  }
  current.phase = 'reporting';
  current.reportAttempts += 1;
  await saveRunState(state);
  // 上报途中 Service Worker 被回收时，靠这个 alarm 把状态机唤醒、用同一个 request_id 重发
  await armPageTimeout(deps);

  const result = await deps.report({
    type: 'CAPTURE_REPORT',
    sellerId: seller.sellerId,
    site: seller.site,
    items: current.items,
    requestId: current.requestId,
    capturedAt: current.capturedAt,
    pageContext: 's=date-desc-rank',
    sellerName: current.sellerName,
  });

  const pages = current.scannedPage;
  const items = current.items.length;
  switch (result.outcome) {
    case 'success':
      recordStore(state, deps, {
        outcome: 'success',
        pages,
        items,
        coverage: current.coverage,
        newAsins: result.newAsins,
        baselineEstablished: result.baselineEstablished,
        duplicate: result.duplicate,
      });
      return advance(state, deps);
    case 'rejected':
      recordStore(state, deps, { outcome: 'failed', reason: 'report_rejected', message: result.message, pages, items });
      return advance(state, deps);
    case 'idempotency_conflict':
      recordStore(state, deps, { outcome: 'failed', reason: 'report_conflict', message: result.message, pages, items });
      return advance(state, deps);
    case 'network_error':
      recordStore(state, deps, { outcome: 'failed', reason: 'report_network', pages, items });
      return advance(state, deps);
    default:
      // 令牌缺失、过期或同步失败：后面每一家都会同样失败，整轮停止（design 决策 4）
      return endRun(state, deps, 'stopped', 'auth', result.outcome);
  }
}

/** 处理第 `current.page` 页的解析结果。 */
export async function handleScan(state: RunState, scan: PageScan, deps: RunnerDeps): Promise<RunState> {
  const current = state.current;
  if (!current || current.phase !== 'collecting') return state;
  // 翻页后旧文档仍在回答（新页面还没加载完）：忽略，等新页面自己的 complete 事件
  if (scan.docId && scan.docId === current.lastDocId) return state;
  current.scannedPage = current.page;
  current.lastDocId = scan.docId;

  const site = state.queue[state.index]?.site;
  const decision = decideAfterPage({
    scan,
    page: current.page,
    maxPages: state.pages,
    accumulated: current.items,
    cap: state.cap,
    site,
    deliveryTarget: site ? state.deliveryTargets?.[site] : undefined,
  });
  if (scan.kind === 'ok' && scan.sellerName && !current.sellerName) current.sellerName = scan.sellerName;
  if (scan.kind === 'ok') {
    // 每页同步覆盖，读不到时清空：不能把上一页／上一家读到的位置当成当前页的实际位置（PR #207 Codex P2）
    state.deliverySite = site;
    state.deliveryText = scan.deliveryText;
  }

  switch (decision.action) {
    case 'stop_run': {
      // 当前店不上报；之前已成功提交的店保留在 results 里
      const detail = decision.reason !== 'blocked' && scan.kind === 'ok' ? scan.deliveryText : undefined;
      recordStore(state, deps, {
        outcome: 'stopped',
        reason: decision.reason,
        message: detail,
        pages: current.page,
        items: current.items.length,
      });
      return endRun(state, deps, 'stopped', decision.reason, detail);
    }
    case 'fail_store':
      recordStore(state, deps, { outcome: 'failed', reason: decision.reason, pages: current.page, items: current.items.length });
      return advance(state, deps);
    case 'next_page': {
      const seller = state.queue[state.index]!;
      current.items = decision.items;
      current.page += 1;
      await saveRunState(state);
      const url = buildStorefrontUrl(seller.sellerId, seller.site, current.page)!;
      try {
        await chrome.tabs.update(current.tabId!, { url });
      } catch {
        recordStore(state, deps, { outcome: 'failed', reason: 'tab_error', pages: current.scannedPage, items: current.items.length });
        return advance(state, deps);
      }
      await armPageTimeout(deps);
      return state;
    }
    case 'report':
      current.items = decision.items;
      current.coverage = decision.coverage;
      await closeTab(current.tabId);
      current.tabId = undefined;
      return reportCurrent(state, deps);
  }
}

// ---------- 对外入口（background 调用） ----------

export type StartOutcome =
  | { started: true }
  | { started: false; reason: 'already_running' | RunStopReason };

export function startRun(trigger: RunTrigger, deps: RunnerDeps): Promise<StartOutcome> {
  return serial(async () => {
    const existing = await loadRunState();
    if (existing?.status === 'running') return { started: false, reason: 'already_running' };

    const settings = await deps.getSettings();
    const base: RunState = {
      runId: crypto.randomUUID(),
      trigger,
      status: 'running',
      startedAt: iso(deps),
      pages: settings.pages,
      deliveryTargets: settings.deliveryTargets,
      cap: 0,
      queue: [],
      index: 0,
      results: [],
    };

    // 读不到服务端上限，本轮不开始（沿用 #166 D4：不回落本地默认值）
    const cap = await deps.getCap();
    if (cap.outcome !== 'ok') {
      await endRun(base, deps, 'stopped', 'config_unavailable');
      return { started: false, reason: 'config_unavailable' };
    }
    base.cap = cap.maxReportItems;
    if (cap.selectors && cap.selectorsVersion) {
      base.selectors = cap.selectors;
      base.selectorsVersion = cap.selectorsVersion;
    }

    const sellers = await deps.getSellers(settings.maxStores);
    if (sellers.kind !== 'ok') {
      const reason: RunStopReason = sellers.kind === 'auth_error' ? 'auth' : 'watch_list_unavailable';
      await endRun(base, deps, 'stopped', reason);
      return { started: false, reason };
    }
    base.queue = sellers.sellers;
    if (base.queue.length === 0) {
      await endRun(base, deps, 'finished');
      return { started: true };
    }

    // 先建看门狗再落「运行中」状态：中间被回收时，看门狗会发现没有运行中的一轮并自行清除
    await chrome.alarms.create(ALARM_WATCHDOG, { periodInMinutes: 1 });
    await saveRunState(base);
    await openCurrentStore(base, deps);
    return { started: true };
  });
}

export function stopRun(deps: RunnerDeps): Promise<boolean> {
  return serial(async () => {
    const state = await loadRunState();
    if (state?.status !== 'running') return false;
    await endRun(state, deps, 'stopped', 'manual_stop');
    return true;
  });
}

/** 是否是当前自动采集打开的标签页（content script 据此决定不渲染人工面板）。 */
export async function isAutoTab(tabId: number | undefined): Promise<boolean> {
  if (tabId === undefined) return false;
  const state = await loadRunState();
  return state?.status === 'running' && state.current?.tabId === tabId;
}

/** 向 content script 请求解析；它可能还没注册监听器，短暂重试几次。 */
async function requestScan(tabId: number, deps: RunnerDeps, selectors?: SelectorOverrides): Promise<PageScan | null> {
  const request: AutoScanRequest = selectors ? { type: 'AUTO_SCAN', selectors } : { type: 'AUTO_SCAN' };
  for (let attempt = 0; attempt <= SCAN_RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      const response = (await chrome.tabs.sendMessage(tabId, request)) as PageScan | undefined;
      if (response && (response.kind === 'ok' || response.kind === 'blocked')) return response;
    } catch {
      // content script 未注入或尚未就绪
    }
    const delay = SCAN_RETRY_DELAYS_MS[attempt];
    if (delay !== undefined) await deps.sleep(delay);
  }
  return null;
}

export function onTabComplete(tabId: number, deps: RunnerDeps): Promise<void> {
  return serial(async () => {
    const state = await loadRunState();
    const current = state?.current;
    if (!state || state.status !== 'running' || !current) return;
    if (current.tabId !== tabId || current.phase !== 'collecting') return;
    if (current.scannedPage >= current.page) return; // 同一页的重复 complete 事件

    const scan = await requestScan(tabId, deps, state.selectors);
    // 拿不到 content script 的回答：可能是重复事件恰好落在新旧页面交替之间，也可能被重定向到
    // 验证码等不注入的地址。这里不下结论，交给已设好的页面超时：新页面加载完会再来一次
    // complete；一直没有，超时就按访问受限停止整轮。
    if (!scan) return;
    await handleScan(state, scan, deps);
  });
}

export function onTabRemoved(tabId: number, deps: RunnerDeps): Promise<void> {
  return serial(async () => {
    const state = await loadRunState();
    const current = state?.current;
    if (!state || state.status !== 'running' || !current) return;
    if (current.tabId !== tabId || current.phase !== 'collecting') return;
    // 采集中的标签页被手动关掉：这家店本次失败，继续下一家
    current.tabId = undefined;
    recordStore(state, deps, { outcome: 'failed', reason: 'tab_error', message: 'tab_closed', pages: current.scannedPage, items: current.items.length });
    await advance(state, deps);
  });
}

export function onRunAlarm(name: string, deps: RunnerDeps): Promise<void> {
  return serial(async () => {
    const state = await loadRunState();
    if (!state || state.status !== 'running') {
      if (name === ALARM_WATCHDOG) await chrome.alarms.clear(ALARM_WATCHDOG); // 残留的看门狗
      return;
    }

    if (name === ALARM_WATCHDOG) {
      // 运行中却没有任何下一步的唤醒源：补上，让这一轮继续推进
      if (!state.current) {
        if (!(await chrome.alarms.get(ALARM_NEXT_STORE))) {
          await chrome.alarms.create(ALARM_NEXT_STORE, { when: deps.now().getTime() });
        }
      } else if (!(await chrome.alarms.get(ALARM_TIMEOUT))) {
        await armPageTimeout(deps);
      }
      return;
    }

    if (name === ALARM_NEXT_STORE) {
      if (!state.current) await openCurrentStore(state, deps);
      return;
    }
    if (name === ALARM_TIMEOUT) {
      const current = state.current;
      if (!current) return;
      if (current.phase === 'reporting') {
        // 上报途中被回收：用同一个 request_id 重发，有上限
        if (current.reportAttempts >= MAX_REPORT_ATTEMPTS) {
          recordStore(state, deps, { outcome: 'failed', reason: 'report_network', pages: current.scannedPage, items: current.items.length });
          await advance(state, deps);
          return;
        }
        await reportCurrent(state, deps);
        return;
      }
      recordStore(state, deps, { outcome: 'stopped', reason: 'page_timeout', pages: current.page, items: current.items.length });
      await endRun(state, deps, 'stopped', 'page_timeout');
    }
  });
}

/**
 * Chrome 重启后接着做（design 决策 3 的恢复规则）：采集阶段的店从第 1 页重来，
 * 上报阶段的用同一个 request_id 重发；当前没有店且等待 alarm 丢失时直接开下一家。
 */
export function resumeAfterStartup(deps: RunnerDeps): Promise<void> {
  return serial(async () => {
    const state = await loadRunState();
    if (!state || state.status !== 'running') return;
    const current = state.current;
    if (!current) {
      const pending = await chrome.alarms.get(ALARM_NEXT_STORE);
      if (!pending) await openCurrentStore(state, deps);
      return;
    }
    if (current.phase === 'reporting') {
      await reportCurrent(state, deps);
      return;
    }
    await closeTab(current.tabId);
    state.current = undefined;
    await openCurrentStore(state, deps);
  });
}
