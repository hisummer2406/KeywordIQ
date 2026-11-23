// 读取当前令牌对应账号的 Amazon 关注清单（POST /api/competitor/watches，ProTable 分页契约）。
// 自动采集每轮开始时调用一次；任何失败都返回明确的类型，不返回空清单冒充「没有关注」。

export interface WatchedSeller {
  sellerId: string;
  site: string;
}

export type WatchListOutcome =
  | { kind: 'ok'; sellers: WatchedSeller[] }
  | { kind: 'auth_error' }
  | { kind: 'error'; status?: number };

const PER_PAGE = 200;
const MAX_PAGES = 20; // 关注上限是千级，200×20 足够；防止服务端异常时无限翻页

interface WatchListPage {
  data?: { seller_id?: unknown; site?: unknown }[];
  total?: unknown;
}

export async function fetchWatchedSellers(
  baseUrl: string,
  token: string,
  limit: number,
  fetchImpl: typeof fetch = fetch,
): Promise<WatchListOutcome> {
  const sellers: WatchedSeller[] = [];
  const seen = new Set<string>();

  for (let page = 1; page <= MAX_PAGES && sellers.length < limit; page += 1) {
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}/api/competitor/watches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ page, perPage: PER_PAGE }),
      });
    } catch {
      return { kind: 'error' };
    }
    if (response.status === 401 || response.status === 403) return { kind: 'auth_error' };
    if (!response.ok) return { kind: 'error', status: response.status };

    let body: WatchListPage;
    try {
      body = (await response.json()) as WatchListPage;
    } catch {
      return { kind: 'error', status: response.status };
    }
    const rows = Array.isArray(body.data) ? body.data : [];
    for (const row of rows) {
      if (typeof row.seller_id !== 'string' || typeof row.site !== 'string') continue;
      const key = `${row.site}:${row.seller_id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      sellers.push({ sellerId: row.seller_id, site: row.site });
      if (sellers.length >= limit) break;
    }
    const total = typeof body.total === 'number' ? body.total : 0;
    if (rows.length < PER_PAGE || page * PER_PAGE >= total) break;
  }

  return { kind: 'ok', sellers };
}
