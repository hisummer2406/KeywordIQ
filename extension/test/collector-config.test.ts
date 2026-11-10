import { describe, expect, it, vi } from 'vitest';
import { fetchCollectorConfig } from '../src/collector-config';

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

describe('fetchCollectorConfig（#166 D4）', () => {
  it('带令牌请求 collector-config，返回服务端上限', async () => {
    const fetchImpl = respond(200, { max_report_items: 1000 });
    const result = await fetchCollectorConfig('http://localhost:8000', 'tok', { fetchImpl });

    expect(result).toEqual({ kind: 'ok', maxReportItems: 1000 });
    const [url, init] = vi.mocked(fetchImpl).mock.calls[0]!;
    expect(url).toBe('http://localhost:8000/api/competitor/collector-config');
    expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer tok');
  });

  it('401 单独返回 auth_error，交给调用方去同步令牌', async () => {
    expect(await fetchCollectorConfig('http://x', 't', { fetchImpl: respond(401, {}) })).toEqual({
      kind: 'auth_error',
    });
  });

  it('其他失败、或返回值不是正整数时一律 unavailable', async () => {
    for (const [status, body] of [
      [500, {}],
      [200, { max_report_items: 0 }],
      [200, { max_report_items: 1.5 }],
      [200, { max_report_items: '1000' }],
      [200, {}],
    ] as const) {
      expect(await fetchCollectorConfig('http://x', 't', { fetchImpl: respond(status, body) })).toEqual({
        kind: 'unavailable',
      });
    }
  });

  it('网络异常时 unavailable，不抛出', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;
    expect(await fetchCollectorConfig('http://x', 't', { fetchImpl })).toEqual({ kind: 'unavailable' });
  });
});
