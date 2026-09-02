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
};

describe('handleCapture', () => {
  beforeEach(async () => {
    installChromeStub();
    stubRuntimeMessaging();
    vi.resetModules();
  });

  it('未配置 token 时返回 missing_token，不发起请求', async () => {
    const { sendReport } = await import('../src/report-client');
    const { handleCapture } = await import('../src/background');

    const result = await handleCapture(baseMessage);

    expect(result).toEqual({ outcome: 'missing_token' });
    expect(sendReport).not.toHaveBeenCalled();
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
