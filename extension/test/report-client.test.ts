import { describe, expect, it, vi } from 'vitest';
import { sendReport } from '../src/report-client';
import type { AsinReportRequest } from '../src/build-report';
import { installChromeStub } from './chrome-stub';

const IDEMPOTENCY_LOG_KEY = 'keywordiq_idempotency_conflict_log';

const body: AsinReportRequest = {
  request_id: 'req-1',
  seller_id: 'A1TZ946F8HZTNY',
  site: 'US',
  items: [{ asin: 'B0TESTASIN1' }],
};

const noopSleep = async () => undefined;

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('sendReport', () => {
  it('2xx 返回 success 并透传解析后的响应体', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        report_id: 1,
        accepted: 1,
        new_asins: 1,
        baseline_established: false,
        duplicate_of: null,
        rejected: [],
      }),
    );

    const outcome = await sendReport('http://localhost:8000', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome.kind).toBe('success');
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:8000/api/competitor/asin-reports');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(JSON.parse(init.body as string)).toEqual(body);
  });

  it('401 直接返回 auth_error，不重试', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(401, { message: '无效令牌' }));

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome).toEqual({ kind: 'auth_error' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('403 同样返回 auth_error，不重试', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(403, { message: '无权限' }));
    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });
    expect(outcome).toEqual({ kind: 'auth_error' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('409 返回 idempotency_conflict 并透传 message，不重试，且把原始响应体写入环形缓冲区', async () => {
    installChromeStub();
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse(409, { message: '幂等键已被用于另一次上报', code: 'competitor.idempotency_key_reused' }));

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome).toEqual({ kind: 'idempotency_conflict', message: '幂等键已被用于另一次上报' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    const stored = await chrome.storage.local.get(IDEMPOTENCY_LOG_KEY);
    const log = stored[IDEMPOTENCY_LOG_KEY] as unknown[];
    expect(log).toHaveLength(1);
    expect(log[0]).toMatchObject({
      requestId: 'req-1',
      status: 409,
      responseBody: { message: '幂等键已被用于另一次上报', code: 'competitor.idempotency_key_reused' },
    });
  });

  it('409 环形缓冲区超出容量时丢弃最旧的记录，只保留最近 20 条', async () => {
    installChromeStub();
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(409, { message: 'x' }));

    for (let i = 0; i < 25; i += 1) {
      await sendReport('http://x', 'tok', { ...body, request_id: `req-${i}` }, { fetchImpl, sleep: noopSleep });
    }

    const stored = await chrome.storage.local.get(IDEMPOTENCY_LOG_KEY);
    const log = stored[IDEMPOTENCY_LOG_KEY] as { requestId: string }[];
    expect(log).toHaveLength(20);
    expect(log[0]?.requestId).toBe('req-5');
    expect(log[19]?.requestId).toBe('req-24');
  });

  it('422 返回 rejected 并透传 message，不重试', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(422, { message: '该卖家不在你的关注清单中' }));

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome).toEqual({ kind: 'rejected', message: '该卖家不在你的关注清单中' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('网络错误按退避重试 3 次后仍失败，返回 network_error', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('network down'));
    const sleep = vi.fn().mockResolvedValue(undefined);

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep, maxRetries: 3 });

    expect(outcome).toEqual({ kind: 'network_error' });
    expect(fetchImpl).toHaveBeenCalledTimes(4); // 首次 + 3 次重试
    expect(sleep).toHaveBeenCalledTimes(3);
  });

  it('前两次网络错误、第三次成功：返回 success，且成功前的重试都复用同一份请求体', async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          report_id: 1,
          accepted: 1,
          new_asins: 0,
          baseline_established: false,
          duplicate_of: 1,
          rejected: [],
        }),
      );

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome.kind).toBe('success');
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    const bodies = fetchImpl.mock.calls.map((call) => (call[1] as RequestInit).body);
    expect(new Set(bodies).size).toBe(1); // 三次请求体逐字节相同，同一个 request_id
  });

  it('5xx 视为瞬时失败按同一套退避重试', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(500, { message: '服务器内部错误' }));
    const sleep = vi.fn().mockResolvedValue(undefined);

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep, maxRetries: 2 });

    expect(outcome).toEqual({ kind: 'network_error' });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('请求挂起超过超时时间后按可重试的瞬时失败处理（退避重试）', async () => {
    // fetchImpl 模拟"请求挂起、永不自行结束"：只在收到 AbortSignal 的 abort 事件
    // 时才 reject，真实还原"超时后按 1s/2s/4s 重试"这条规范承诺（PR #68 复审 P2）。
    const fetchImpl = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init.signal as AbortSignal;
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    });
    const sleep = vi.fn().mockResolvedValue(undefined);

    const outcome = await sendReport('http://x', 'tok', body, {
      fetchImpl,
      sleep,
      maxRetries: 1,
      timeoutMs: 10,
    });

    expect(outcome).toEqual({ kind: 'network_error' });
    expect(fetchImpl).toHaveBeenCalledTimes(2); // 首次 + 1 次重试，均因超时被 abort
    expect(sleep).toHaveBeenCalledTimes(1);
  });

  it('超时重试后成功：返回 success', async () => {
    const fetchImpl = vi.fn().mockImplementationOnce((_url: string, init: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        const signal = init.signal as AbortSignal;
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    }).mockResolvedValueOnce(
      jsonResponse(200, {
        report_id: 1,
        accepted: 1,
        new_asins: 1,
        baseline_established: false,
        duplicate_of: null,
        rejected: [],
      }),
    );
    const sleep = vi.fn().mockResolvedValue(undefined);

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep, timeoutMs: 10 });

    expect(outcome.kind).toBe('success');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
