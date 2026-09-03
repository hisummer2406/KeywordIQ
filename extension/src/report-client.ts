// 发起上报请求，按 design.md 决策 5 分类处理失败。所有失败响应遵循仓库统一的
// 错误契约 {"success": false, "code": "...", "message": "...", "request_id": "..."}
// （app/common/error_handlers.py），因此各类错误都能从响应体的 message 拿到
// 面向用户的中文提示，不需要按状态码各写一套文案解析逻辑。

import type { AsinReportRequest } from './build-report';
import { appendIdempotencyConflictLog } from './idempotency-log';

export interface AsinReportResponse {
  report_id: number;
  accepted: number;
  new_asins: number;
  baseline_established: boolean;
  duplicate_of: number | null;
  rejected: { asin: string; reason: string }[];
}

export type ReportOutcome =
  | { kind: 'success'; response: AsinReportResponse }
  | { kind: 'auth_error' }
  | { kind: 'idempotency_conflict'; message: string }
  | { kind: 'rejected'; message: string }
  | { kind: 'network_error' };

export interface SendReportOptions {
  /** 可注入的 fetch 实现，供测试替身使用；默认取全局 fetch。 */
  fetchImpl?: typeof fetch;
  /** 可注入的等待函数，供测试跳过真实退避延时；默认真实 setTimeout。 */
  sleep?: (ms: number) => Promise<void>;
  /** 重试次数上限（不含首次尝试），默认 3。 */
  maxRetries?: number;
  /** 每次重试前的退避毫秒数，长度应 >= maxRetries；默认 [1000, 2000, 4000]。 */
  backoffMs?: number[];
  /** 每次尝试的超时毫秒数，默认 15000。超时按可重试的瞬时失败处理。 */
  timeoutMs?: number;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const DEFAULT_TIMEOUT_MS = 15000;

/**
 * 发送一次已经冻结好的请求体。**调用方负责保证同一个 body 在所有重试之间不变**
 * （design.md 决策 3）——本函数只管发送与分类结果，不生成也不修改 request_id。
 */
export async function sendReport(
  baseUrl: string,
  token: string,
  body: AsinReportRequest,
  options: SendReportOptions = {},
): Promise<ReportOutcome> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const sleep = options.sleep ?? defaultSleep;
  const maxRetries = options.maxRetries ?? 3;
  const backoffMs = options.backoffMs ?? [1000, 2000, 4000];
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    let response: Response;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      try {
        response = await fetchImpl(`${baseUrl}/api/competitor/asin-reports`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
      } catch {
        // 网络层错误（断网、DNS 失败等）与超时（AbortController 触发后 fetch 同样
        // 以异常告终）在这里表现相同，都视为可重试的瞬时失败——规范承诺的"超时后
        // 按 1s/2s/4s 重试"正是靠这个 AbortSignal 触发的（PR #68 复审 P2）。
        if (attempt < maxRetries) {
          await sleep(backoffMs[attempt] ?? backoffMs[backoffMs.length - 1] ?? 4000);
          continue;
        }
        return { kind: 'network_error' };
      }
    } finally {
      clearTimeout(timeoutId);
    }

    if (response.ok) {
      const parsed = (await response.json()) as AsinReportResponse;
      return { kind: 'success', response: parsed };
    }

    if (response.status === 401 || response.status === 403) {
      return { kind: 'auth_error' };
    }

    if (response.status === 409) {
      const raw = await readJsonSafely(response);
      // 原始响应体写进有界环形缓冲区（design.md 决策 5 / tasks.md 6.4）：这是
      // 定位理论上"不应发生"的 request_id 冲突所需的证据。写失败不影响本次上报
      // 结果的判定，吞掉异常即可。
      await appendIdempotencyConflictLog({
        requestId: body.request_id,
        status: response.status,
        responseBody: raw,
      }).catch(() => undefined);
      return { kind: 'idempotency_conflict', message: extractMessage(raw, response.status) };
    }

    if (response.status === 422) {
      const raw = await readJsonSafely(response);
      return { kind: 'rejected', message: extractMessage(raw, response.status) };
    }

    // 5xx 与其余未分类状态码：视为瞬时失败，按同一套退避重试——服务端偶发抖动
    // 不该让运营立刻放弃这次采集结果。
    if (attempt < maxRetries) {
      await sleep(backoffMs[attempt] ?? backoffMs[backoffMs.length - 1] ?? 4000);
      continue;
    }
    return { kind: 'network_error' };
  }

  // 循环内部每条路径都已 return（要么在 maxRetries 次重试后返回失败，要么提前
  // 成功返回）；这一行只是让 TypeScript 满意，永远不会真正执行到。
  return { kind: 'network_error' };
}

async function readJsonSafely(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractMessage(raw: unknown, status: number): string {
  if (raw && typeof raw === 'object' && typeof (raw as { message?: unknown }).message === 'string') {
    return (raw as { message: string }).message;
  }
  return `请求失败（HTTP ${status}）`;
}
