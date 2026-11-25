import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installChromeStub } from './chrome-stub';
import type { PageScan } from '../src/auto-plan';
import type { CaptureMessage, CaptureResult } from '../src/messages';

type Runner = typeof import('../src/auto-runner');

interface Harness {
  runner: Runner;
  deps: import('../src/auto-runner').RunnerDeps;
  report: ReturnType<typeof vi.fn>;
  scans: Map<string, PageScan>;
  openedUrls: string[];
  alarms: Map<string, { when?: number }>;
  removedTabs: number[];
}

const SUCCESS: CaptureResult = { outcome: 'success', accepted: 2, newAsins: 1, baselineEstablished: false, duplicate: false };

/** 按「卖家 + 页码」给出 content script 的回答；没配的页按空页处理。 */
function scanKey(url: string): string {
  const u = new URL(url);
  return `${u.searchParams.get('me')}:${u.searchParams.get('page') ?? '1'}`;
}

async function setup(sellers: { sellerId: string; site: string }[], pages = 2): Promise<Harness> {
  installChromeStub();
  vi.resetModules();

  const scans = new Map<string, PageScan>();
  const openedUrls: string[] = [];
  const tabUrls = new Map<number, string>();
  const alarms = new Map<string, { when?: number }>();
  const removedTabs: number[] = [];
  let nextTabId = 100;

  const chromeGlobal = (globalThis as unknown as { chrome: Record<string, unknown> }).chrome;
  chromeGlobal.tabs = {
    async create({ url }: { url: string }) {
      const id = nextTabId++;
      tabUrls.set(id, url);
      openedUrls.push(url);
      return { id };
    },
    async update(id: number, { url }: { url: string }) {
      tabUrls.set(id, url);
      openedUrls.push(url);
      return { id };
    },
    async remove(id: number) {
      removedTabs.push(id);
      tabUrls.delete(id);
    },
    async sendMessage(id: number) {
      const url = tabUrls.get(id);
      if (!url) throw new Error('no tab');
      const scan: PageScan = scans.get(scanKey(url)) ?? { kind: 'ok', items: [], hasNext: false };
      // 没显式给 deliveryText 的页按专用 Profile 已设好 US / 10001 处理
      return scan.kind === 'ok' && !('deliveryText' in scan) ? { ...scan, deliveryText: 'New York 10001' } : scan;
    },
  };
  chromeGlobal.alarms = {
    async create(name: string, info: { when?: number }) {
      alarms.set(name, info);
    },
    async clear(name: string) {
      return alarms.delete(name);
    },
    async get(name: string) {
      return alarms.has(name) ? { name, ...alarms.get(name) } : undefined;
    },
  };

  const runner = await import('../src/auto-runner');
  const report = vi.fn(async (_m: CaptureMessage) => SUCCESS);
  const deps = {
    now: () => new Date('2026-11-24T00:00:00Z'),
    random: () => 0,
    getCap: async () => ({ outcome: 'ok' as const, maxReportItems: 1000 }),
    getSellers: async () => ({ kind: 'ok' as const, sellers }),
    report,
    getSettings: async () => ({ pages, maxStores: 20, deliveryTargets: { US: '10001' } }),
    sleep: async () => undefined,
  };
  return { runner, deps, report, scans, openedUrls, alarms, removedTabs };
}

/** 模拟当前标签页加载完成。 */
async function completeCurrentTab(h: Harness): Promise<void> {
  const state = await h.runner.loadRunState();
  const tabId = state?.current?.tabId;
  if (tabId === undefined) throw new Error('当前没有采集中的标签页');
  await h.runner.onTabComplete(tabId, h.deps);
}

describe('自动采集状态机', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('两页合并后只上报一次，然后等待下一家', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }, { asin: 'B02' }], hasNext: true });
    h.scans.set('S1:2', { kind: 'ok', items: [{ asin: 'B02' }, { asin: 'B03' }], hasNext: true });

    expect(await h.runner.startRun('manual', h.deps)).toEqual({ started: true });
    await completeCurrentTab(h); // 第 1 页
    await completeCurrentTab(h); // 第 2 页

    expect(h.report).toHaveBeenCalledTimes(1);
    const message = h.report.mock.calls[0]![0] as CaptureMessage;
    expect(message.sellerId).toBe('S1');
    expect(message.items.map((i) => i.asin)).toEqual(['B01', 'B02', 'B03']);
    expect(h.alarms.has(h.runner.ALARM_NEXT_STORE)).toBe(true);

    const state = await h.runner.loadRunState();
    expect(state?.index).toBe(1);
    expect(state?.results[0]).toMatchObject({ sellerId: 'S1', outcome: 'success', pages: 2, items: 3, coverage: 'limited' });
  });

  it('自然到达末页记为覆盖完整', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }], 3);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true });
    h.scans.set('S1:2', { kind: 'ok', items: [{ asin: 'B02' }], hasNext: false });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);
    await completeCurrentTab(h);

    expect(h.openedUrls.some((u) => u.includes('page=3'))).toBe(false);
    expect(h.report).toHaveBeenCalledTimes(1);
    const state = await h.runner.loadRunState();
    expect(state?.results[0]).toMatchObject({ outcome: 'success', pages: 2, items: 2, coverage: 'complete' });
  });

  it('前一页提示有下一页、后页没有有效 ASIN：记异常缺页，不提交残缺批次，继续下一家', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true });
    // S1:2 未配置 → 空页
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);
    await completeCurrentTab(h);

    expect(h.report).not.toHaveBeenCalled();
    const state = await h.runner.loadRunState();
    expect(state?.status).toBe('running');
    expect(state?.results[0]).toMatchObject({ sellerId: 'S1', outcome: 'failed', reason: 'missing_page', pages: 2, items: 1 });
  });

  it('第二家发现配送不符：整轮停止，第一家已提交的结果保留，第二家不上报', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
      { sellerId: 'S3', site: 'US' },
    ], 1);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false });
    h.scans.set('S2:1', { kind: 'ok', items: [{ asin: 'B02' }], hasNext: false, deliveryText: 'Berlin 10115, Germany' });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);
    await h.runner.onRunAlarm(h.runner.ALARM_NEXT_STORE, h.deps);
    await completeCurrentTab(h);

    expect(h.report).toHaveBeenCalledTimes(1);
    expect((h.report.mock.calls[0]![0] as CaptureMessage).sellerId).toBe('S1');
    const state = await h.runner.loadRunState();
    expect(state).toMatchObject({
      status: 'stopped',
      stopReason: 'wrong_delivery',
      stopDetail: 'Berlin 10115, Germany',
      deliverySite: 'US',
      deliveryTargets: { US: '10001' },
    });
    expect(state?.results).toHaveLength(2);
    expect(state?.results[0]).toMatchObject({ sellerId: 'S1', outcome: 'success', coverage: 'complete' });
    expect(state?.results[1]).toMatchObject({ sellerId: 'S2', outcome: 'stopped', reason: 'wrong_delivery' });
    expect(h.openedUrls.some((u) => u.includes('me=S3'))).toBe(false);
  });

  it('配送位置读不到或只剩 Deliver to：记「配送位置未确认」，在该店停止，不上报', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false, deliveryText: 'Deliver to' });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);

    expect(h.report).not.toHaveBeenCalled();
    expect(await h.runner.loadRunState()).toMatchObject({
      status: 'stopped',
      stopReason: 'delivery_unconfirmed',
      stopDetail: 'Deliver to',
      results: [{ sellerId: 'S1', outcome: 'stopped', reason: 'delivery_unconfirmed' }],
    });
  });

  it('翻到第 2 页时配送位置漂移成别处：整轮停止，第 1 页的条目不上报', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true });
    h.scans.set('S1:2', { kind: 'ok', items: [{ asin: 'B02' }], hasNext: false, deliveryText: 'China' });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);
    await completeCurrentTab(h);

    expect(h.report).not.toHaveBeenCalled();
    expect(await h.runner.loadRunState()).toMatchObject({ status: 'stopped', stopReason: 'wrong_delivery' });
  });

  it('没有配置该站点的目标邮编：无法确认，停止', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    h.deps.getSettings = async () => ({ pages: 2, maxStores: 20, deliveryTargets: {} });
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);
    expect(h.report).not.toHaveBeenCalled();
    expect(await h.runner.loadRunState()).toMatchObject({ status: 'stopped', stopReason: 'delivery_unconfirmed' });
  });

  it('遇到拦截页立即停止整轮，不再访问后面的店', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    h.scans.set('S1:1', { kind: 'blocked' });

    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);

    const state = await h.runner.loadRunState();
    expect(state).toMatchObject({ status: 'stopped', stopReason: 'blocked' });
    expect(h.report).not.toHaveBeenCalled();
    expect(h.openedUrls.every((u) => u.includes('me=S1'))).toBe(true);
    expect(h.alarms.size).toBe(0);
    const history = await h.runner.loadRunHistory();
    expect(history.at(-1)?.stopReason).toBe('blocked');
  });

  it('第 1 页为空：这家店不上报，继续下一家', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);

    expect(h.report).not.toHaveBeenCalled();
    const state = await h.runner.loadRunState();
    expect(state?.results[0]).toMatchObject({ outcome: 'failed', reason: 'empty_page' });
    expect(state?.status).toBe('running');

    await h.runner.onRunAlarm(h.runner.ALARM_NEXT_STORE, h.deps);
    expect(h.openedUrls.at(-1)).toContain('me=S2');
  });

  it('同一页的重复 complete 事件只处理一次', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }], 1);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false });
    await h.runner.startRun('manual', h.deps);
    const tabId = (await h.runner.loadRunState())!.current!.tabId!;

    await Promise.all([h.runner.onTabComplete(tabId, h.deps), h.runner.onTabComplete(tabId, h.deps)]);
    expect(h.report).toHaveBeenCalledTimes(1);
  });

  it('翻页后旧页面仍在回答：忽略，等新页面的结果（PR #206 Codex P2）', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }], 2);
    await h.runner.startRun('manual', h.deps);

    let state = (await h.runner.loadRunState())!;
    state = await h.runner.handleScan(
      state,
      { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true, docId: 'doc-page-1', deliveryText: 'New York 10001' },
      h.deps,
    );
    expect(state.current?.page).toBe(2);

    // 第 1 页的重复 complete 事件：回答者仍是第 1 页的文档
    state = await h.runner.handleScan(
      state,
      { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true, docId: 'doc-page-1', deliveryText: 'New York 10001' },
      h.deps,
    );
    expect(h.report).not.toHaveBeenCalled();
    expect(state.current?.phase).toBe('collecting');

    // 真正的第 2 页加载完成
    await h.runner.handleScan(
      state,
      { kind: 'ok', items: [{ asin: 'B02' }], hasNext: true, docId: 'doc-page-2', deliveryText: 'New York 10001' },
      h.deps,
    );
    expect(h.report).toHaveBeenCalledTimes(1);
    expect((h.report.mock.calls[0]![0] as CaptureMessage).items.map((i) => i.asin)).toEqual(['B01', 'B02']);
  });

  it('拿不到 content script 回答时不立即停止，交给页面超时', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    const tabId = (await h.runner.loadRunState())!.current!.tabId!;
    const chromeTabs = (globalThis as unknown as { chrome: { tabs: { sendMessage: unknown } } }).chrome.tabs;
    chromeTabs.sendMessage = async () => {
      throw new Error('Receiving end does not exist');
    };

    await h.runner.onTabComplete(tabId, h.deps);
    expect((await h.runner.loadRunState())?.status).toBe('running');
    expect(h.alarms.has(h.runner.ALARM_TIMEOUT)).toBe(true);
  });

  it('看门狗：运行中却没有下一步 alarm 时补建（PR #206 Codex P2）', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    await h.runner.startRun('manual', h.deps);
    expect(h.alarms.has(h.runner.ALARM_WATCHDOG)).toBe(true);
    await completeCurrentTab(h); // 第 1 页为空 → 这家失败，进入等待下一家

    // 模拟「状态已保存、下一步 alarm 尚未创建」时被回收
    h.alarms.delete(h.runner.ALARM_NEXT_STORE);
    await h.runner.onRunAlarm(h.runner.ALARM_WATCHDOG, h.deps);
    expect(h.alarms.has(h.runner.ALARM_NEXT_STORE)).toBe(true);

    await h.runner.onRunAlarm(h.runner.ALARM_NEXT_STORE, h.deps);
    expect(h.openedUrls.at(-1)).toContain('me=S2');

    // 采集中却丢了超时 alarm：看门狗补上
    h.alarms.delete(h.runner.ALARM_TIMEOUT);
    await h.runner.onRunAlarm(h.runner.ALARM_WATCHDOG, h.deps);
    expect(h.alarms.has(h.runner.ALARM_TIMEOUT)).toBe(true);
  });

  it('一轮结束后看门狗被清除；残留的看门狗触发时自行清除', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    await h.runner.stopRun(h.deps);
    expect(h.alarms.has(h.runner.ALARM_WATCHDOG)).toBe(false);

    h.alarms.set(h.runner.ALARM_WATCHDOG, {});
    await h.runner.onRunAlarm(h.runner.ALARM_WATCHDOG, h.deps);
    expect(h.alarms.has(h.runner.ALARM_WATCHDOG)).toBe(false);
  });

  it('上报途中被中断：恢复时用同一个 request_id 重发，覆盖有限标记保留', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }], 1);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: true });
    // 第一次上报"没有回来"：用一个永不完成的 Promise 模拟 Service Worker 在等待中被回收
    h.report.mockImplementationOnce(() => new Promise<CaptureResult>(() => undefined));

    await h.runner.startRun('manual', h.deps);
    const tabId = (await h.runner.loadRunState())!.current!.tabId!;
    void h.runner.onTabComplete(tabId, h.deps);
    await new Promise((resolve) => setTimeout(resolve, 0));

    const midway = await h.runner.loadRunState();
    expect(midway?.current?.phase).toBe('reporting');
    const firstRequestId = (h.report.mock.calls[0]![0] as CaptureMessage).requestId;

    // 模拟 Service Worker 重启：重新加载模块（串行队列随之清空），由超时 alarm 唤醒
    vi.resetModules();
    const fresh = await import('../src/auto-runner');
    h.report.mockImplementation(async () => SUCCESS);
    await fresh.onRunAlarm(fresh.ALARM_TIMEOUT, h.deps);

    expect(h.report).toHaveBeenCalledTimes(2);
    const first = h.report.mock.calls[0]![0] as CaptureMessage;
    const second = h.report.mock.calls[1]![0] as CaptureMessage;
    expect(second.requestId).toBe(firstRequestId);
    expect(second.capturedAt).toBe(first.capturedAt);
    expect(second.items).toEqual(first.items);
    const done = await fresh.loadRunState();
    expect(done?.status).toBe('finished');
    expect(done?.results[0]).toMatchObject({ outcome: 'success', coverage: 'limited' });
  });

  it('令牌失效：整轮停止', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ], 1);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false });
    h.report.mockImplementationOnce(async () => ({ outcome: 'auth_error' as const }));

    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);

    expect(await h.runner.loadRunState()).toMatchObject({ status: 'stopped', stopReason: 'auth' });
  });

  it('读不到服务端上限：本轮不开始，也不打开任何页面', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    h.deps.getCap = async () => ({ outcome: 'unavailable' });

    expect(await h.runner.startRun('manual', h.deps)).toEqual({ started: false, reason: 'config_unavailable' });
    expect(h.openedUrls).toHaveLength(0);
  });

  it('已有一轮在运行时不会再开一轮', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    expect(await h.runner.startRun('daily', h.deps)).toEqual({ started: false, reason: 'already_running' });
  });

  it('配送地址不是美国：整轮停止，记录页面上的地址，不上报', async () => {
    const h = await setup([
      { sellerId: 'S1', site: 'US' },
      { sellerId: 'S2', site: 'US' },
    ]);
    h.scans.set('S1:1', { kind: 'ok', items: [{ asin: 'B01' }], hasNext: false, deliveryText: 'China' });
    await h.runner.startRun('manual', h.deps);
    await completeCurrentTab(h);

    expect(await h.runner.loadRunState()).toMatchObject({
      status: 'stopped',
      stopReason: 'wrong_delivery',
      stopDetail: 'China',
    });
    expect(h.report).not.toHaveBeenCalled();
  });

  it('页面超时：按访问受限停止整轮', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    await h.runner.onRunAlarm(h.runner.ALARM_TIMEOUT, h.deps);
    expect(await h.runner.loadRunState()).toMatchObject({ status: 'stopped', stopReason: 'page_timeout' });
  });

  it('自动采集的标签页被识别出来，其它标签页不是', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    const tabId = (await h.runner.loadRunState())!.current!.tabId!;
    expect(await h.runner.isAutoTab(tabId)).toBe(true);
    expect(await h.runner.isAutoTab(tabId + 1)).toBe(false);
  });

  it('手动停止会关闭当前标签页', async () => {
    const h = await setup([{ sellerId: 'S1', site: 'US' }]);
    await h.runner.startRun('manual', h.deps);
    const tabId = (await h.runner.loadRunState())!.current!.tabId!;
    expect(await h.runner.stopRun(h.deps)).toBe(true);
    expect(h.removedTabs).toContain(tabId);
    expect(await h.runner.loadRunState()).toMatchObject({ status: 'stopped', stopReason: 'manual_stop' });
  });
});
