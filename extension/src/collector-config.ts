// 读取服务端公布的采集端配置（competitor-asin-ingest「服务端公布单批上限」，#166 D4）。
//
// 单批上限**只**以这里的返回值为准：不回落到任何本地默认值。取不到就让面板禁止开始
// 采集——否则运营可能按一个错的上限翻完很多页，提交时才被服务端整批拒绝。

import { normalizeOverrides, type SelectorOverrides } from './selector-overrides';

export type CollectorConfigOutcome =
  | { kind: 'ok'; maxReportItems: number; selectors?: SelectorOverrides; selectorsVersion?: string }
  | { kind: 'auth_error' }
  | { kind: 'unavailable' };

const DEFAULT_TIMEOUT_MS = 10000;

export async function fetchCollectorConfig(
  baseUrl: string,
  token: string,
  options: { fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<CollectorConfigOutcome> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`${baseUrl}/api/competitor/collector-config`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    });
    if (response.status === 401) return { kind: 'auth_error' };
    if (!response.ok) return { kind: 'unavailable' };
    const body = (await response.json()) as {
      max_report_items?: unknown;
      selectors?: unknown;
      selectors_version?: unknown;
    };
    const value = body?.max_report_items;
    // 只接受正整数：一个看不懂的值当成「取不到」，不拿它去放行或拦截
    if (typeof value !== 'number' || !Number.isInteger(value) || value <= 0) {
      return { kind: 'unavailable' };
    }
    // 选择器覆盖可有可无：形状不对就当没下发，不影响上限（extension-remote-selectors）
    const selectors = normalizeOverrides(body.selectors);
    const version = body.selectors_version;
    if (selectors && typeof version === 'string' && version.length > 0 && version.length <= 64) {
      return { kind: 'ok', maxReportItems: value, selectors, selectorsVersion: version };
    }
    return { kind: 'ok', maxReportItems: value };
  } catch {
    return { kind: 'unavailable' };
  } finally {
    clearTimeout(timeoutId);
  }
}
