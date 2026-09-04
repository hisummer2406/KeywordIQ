import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installChromeStub } from './chrome-stub';

vi.mock('../src/report-client', () => ({
  sendReport: vi.fn(),
}));

// chrome.runtime.onMessage 必须在 import '../src/background' 之前存在，因为
// background.ts 顶层就调用了 addListener。
function stubRuntimeMessaging() {
  (globalThis as { chrome: { storage: unknown; runtime?: unknown } }).chrome.runtime = {
    onMessage: { addListener: vi.fn() },
  };
}

const baseMessage = {
  type: 'CAPTURE_REPORT' as const,
  sellerId: 'A1TZ946F8HZTNY',
  site: 'US',
  items: [{ asin: 'B0TESTASIN1' }],
  requestId: 'frozen-req-1',
  capturedAt: '2026-09-03T00:00:00.000Z',
};

describe('handleCapture', () => {
  beforeEach(async () => {
    installChromeStub();
    stubRuntimeMessaging();
    vi.resetModules();
    // `vi.mock` 的工厂只求值一次，`sendReport` 这个替身因此在整个文件里是同一个，
    // 调用记录会跨用例累积。此前没暴露，纯粹是因为两条 `not.toHaveBeenCalled()`
    // 恰好排在所有会调用它的用例之前——也就是说这个文件隐式依赖用例顺序，在中间
    // 插入任何一条会发请求的用例都会让它们失败，而失败信息（"expected 0, got 4"）
    // 指不到真正的原因。用 clearAllMocks 清掉调用记录；它不清实现，
    // 各用例自己的 mockResolvedValue(Once) 照常生效。
    vi.clearAllMocks();
  });

  /** 让网页来源处于「已授权 + 有标签页 + localStorage 里是这个令牌」的状态。 */
  async function withWebSession(token: string | null): Promise<void> {
    const c = globalThis as any;
    await c.chrome.permissions.request({ origins: ['http://localhost:8000/*'] });
    c.chrome.tabs.query = vi.fn(async () => [{ id: 1 }]);
    c.chrome.scripting.executeScript = vi.fn(async () => [{ result: token }]);
  }

  it('未配置 token 且没有网页标签页时返回 sync_no_tab，不发起请求', async () => {
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');

    const result = await handleCapture(baseMessage);

    // 不再是 missing_token：现在会先试着从网页会话同步一次，且失败原因要能分辨出
    // 是「没开网页」还是「没登录」——两者要运营做的事不同
    expect(result).toEqual({ outcome: 'sync_no_tab' });
    expect(sendReport).not.toHaveBeenCalled();
  });

  it('未配置 token 但网页已登录时自动同步并正常上报，无需手动粘贴', async () => {
    const { setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setApiBaseUrl('http://localhost:8000');
    await withWebSession('synced-token');
    vi.mocked(sendReport).mockResolvedValueOnce({
      kind: 'success',
      response: { accepted: 1, new_asins: 1, baseline_established: false, duplicate_of: null },
    } as any);

    expect(await handleCapture(baseMessage)).toMatchObject({ outcome: 'success' });
    expect(vi.mocked(sendReport).mock.calls[0]![1]).toBe('synced-token');
  });

  it('网页开着但未登录时返回 sync_not_logged_in，与「没开网页」分开', async () => {
    const { setApiBaseUrl } = await import('../src/auth');
    const { handleCapture } = await import('../src/background');
    await setApiBaseUrl('http://localhost:8000');
    await withWebSession(null);

    expect(await handleCapture(baseMessage)).toEqual({ outcome: 'sync_not_logged_in' });
  });

  it('401 后同步到不同令牌：用完全相同的请求体重试一次（幂等键不变，不产生第二条观测）', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('stale-token');
    await setApiBaseUrl('http://localhost:8000');
    await withWebSession('fresh-token');

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'auth_error' } as any);
    vi.mocked(sendReport).mockResolvedValueOnce({
      kind: 'success',
      response: { accepted: 1, new_asins: 0, baseline_established: false, duplicate_of: null },
    } as any);

    expect(await handleCapture(baseMessage)).toMatchObject({ outcome: 'success' });

    const calls = vi.mocked(sendReport).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0]![1]).toBe('stale-token');
    expect(calls[1]![1]).toBe('fresh-token');
    // 重试的请求体必须逐字段相同——尤其 request_id 与 captured_at。重新生成幂等键
    // 会让服务端把这次重试当成另一次观测，一次真实采集被记成两次。
    expect(calls[1]![2]).toEqual(calls[0]![2]);
  });

  it('401 但网页会话也过期（同步回同一个令牌）时不重试', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('same-token');
    await setApiBaseUrl('http://localhost:8000');
    await withWebSession('same-token');

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'auth_error' } as any);

    expect(await handleCapture(baseMessage)).toEqual({ outcome: 'auth_error' });
    // 用同一个坏令牌再打一次纯属浪费一个请求，还让运营多等一轮
    expect(vi.mocked(sendReport)).toHaveBeenCalledTimes(1);
  });

  it('已配置 token 但未配置 API 地址时返回 missing_base_url', async () => {
    const { setAuthToken } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('tok');

    const result = await handleCapture(baseMessage);

    expect(result).toEqual({ outcome: 'missing_base_url' });
    expect(sendReport).not.toHaveBeenCalled();
  });

  it('requestId/capturedAt 直接使用消息里冻结的值，不重新生成（PR #68 二次复审 P1）', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('tok');
    await setApiBaseUrl('http://localhost:8000');
    vi.mocked(sendReport).mockResolvedValue({
      kind: 'success',
      response: {
        report_id: 1,
        accepted: 1,
        new_asins: 0,
        baseline_established: false,
        duplicate_of: 1,
        rejected: [],
      },
    });

    await handleCapture(baseMessage);
    await handleCapture(baseMessage); // 模拟"提交失败后不经过重新采集，直接再点提交上报"

    expect(sendReport).toHaveBeenCalledTimes(2);
    const bodies = vi.mocked(sendReport).mock.calls.map((call) => call[2]);
    expect(bodies[0]?.request_id).toBe('frozen-req-1');
    expect(bodies[1]?.request_id).toBe('frozen-req-1');
    expect(bodies[0]?.captured_at).toBe('2026-09-03T00:00:00.000Z');
    expect(bodies[1]?.captured_at).toBe('2026-09-03T00:00:00.000Z');
  });

  it('两者都配置时按 sendReport 的结果映射为 CaptureResult（成功·新发现）', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('tok');
    await setApiBaseUrl('http://localhost:8000');
    vi.mocked(sendReport).mockResolvedValue({
      kind: 'success',
      response: {
        report_id: 1,
        accepted: 1,
        new_asins: 1,
        baseline_established: true,
        duplicate_of: null,
        rejected: [],
      },
    });

    const result = await handleCapture(baseMessage);

    expect(result).toEqual({
      outcome: 'success',
      accepted: 1,
      newAsins: 1,
      baselineEstablished: true,
      duplicate: false,
    });
  });

  it('duplicate_of 非空时映射为 duplicate: true', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('tok');
    await setApiBaseUrl('http://localhost:8000');
    vi.mocked(sendReport).mockResolvedValue({
      kind: 'success',
      response: {
        report_id: 1,
        accepted: 1,
        new_asins: 0,
        baseline_established: false,
        duplicate_of: 1,
        rejected: [],
      },
    });

    const result = await handleCapture(baseMessage);

    expect(result).toMatchObject({ outcome: 'success', duplicate: true });
  });

  it('透传 auth_error/rejected/idempotency_conflict/network_error', async () => {
    const { setAuthToken, setApiBaseUrl } = await import('../src/auth');
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');
    await setAuthToken('tok');
    await setApiBaseUrl('http://localhost:8000');

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'auth_error' });
    expect(await handleCapture(baseMessage)).toEqual({ outcome: 'auth_error' });

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'rejected', message: 'x' });
    expect(await handleCapture(baseMessage)).toEqual({ outcome: 'rejected', message: 'x' });

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'idempotency_conflict', message: 'y' });
    expect(await handleCapture(baseMessage)).toEqual({
      outcome: 'idempotency_conflict',
      message: 'y',
    });

    vi.mocked(sendReport).mockResolvedValueOnce({ kind: 'network_error' });
    expect(await handleCapture(baseMessage)).toEqual({ outcome: 'network_error' });
  });
});
