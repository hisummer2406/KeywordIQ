import { describe, expect, it, vi } from 'vitest';
import { sendReport } from '../src/report-client';
import type { AsinReportRequest } from '../src/build-report';

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

  it('409 返回 idempotency_conflict 并透传 message，不重试', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse(409, { message: '幂等键已被用于另一次上报', code: 'competitor.idempotency_key_reused' }));

    const outcome = await sendReport('http://x', 'tok', body, { fetchImpl, sleep: noopSleep });

    expect(outcome).toEqual({ kind: 'idempotency_conflict', message: '幂等键已被用于另一次上报' });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
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
});
