import { describe, expect, it, vi } from 'vitest';
import { fetchWatchedSellers } from '../src/watch-client';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('fetchWatchedSellers', () => {
  it('按分页读取并按 limit 截取、去重', async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(
      jsonResponse(200, {
        data: [
          { seller_id: 'A1', site: 'US' },
          { seller_id: 'A1', site: 'US' },
          { seller_id: 'A2', site: 'GB' },
          { seller_id: 'A3', site: 'DE' },
        ],
        total: 4,
      }),
    );
    const result = await fetchWatchedSellers('http://api', 'tok', 2, fetchImpl);
    expect(result).toEqual({
      kind: 'ok',
      sellers: [
        { sellerId: 'A1', site: 'US' },
        { sellerId: 'A2', site: 'GB' },
      ],
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://api/api/competitor/watches',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ page: 1, perPage: 200 }) }),
    );
  });

  it('401 → auth_error，不返回空清单冒充没有关注', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(401, {}));
    expect(await fetchWatchedSellers('http://api', 'tok', 20, fetchImpl)).toEqual({ kind: 'auth_error' });
  });

  it('网络异常 → error', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));
    expect(await fetchWatchedSellers('http://api', 'tok', 20, fetchImpl)).toEqual({ kind: 'error' });
  });
});
