// 发起上报请求，按 design.md 决策 5 分类处理失败。所有失败响应遵循仓库统一的
// 错误契约 {"success": false, "code": "...", "message": "...", "request_id": "..."}
// （app/common/error_handlers.py），因此各类错误都能从响应体的 message 拿到
// 面向用户的中文提示，不需要按状态码各写一套文案解析逻辑。

import type { AsinReportRequest } from './build-report';

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
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

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

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    let response: Response;
    try {
      response = await fetchImpl(`${baseUrl}/api/competitor/asin-reports`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      });
    } catch {
      // 网络层错误（断网、DNS 失败等）：fetch 本身抛异常，视为可重试的瞬时失败。
      if (attempt < maxRetries) {
        await sleep(backoffMs[attempt] ?? backoffMs[backoffMs.length - 1] ?? 4000);
        continue;
      }
      return { kind: 'network_error' };
    }

    if (response.ok) {
      const parsed = (await response.json()) as AsinReportResponse;
      return { kind: 'success', response: parsed };
    }

    if (response.status === 401 || response.status === 403) {
      return { kind: 'auth_error' };
    }

    if (response.status === 409) {
      const message = await readMessage(response);
      return { kind: 'idempotency_conflict', message };
    }

    if (response.status === 422) {
      const message = await readMessage(response);
      return { kind: 'rejected', message };
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

async function readMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { message?: string };
    return body.message ?? `请求失败（HTTP ${response.status}）`;
  } catch {
    return `请求失败（HTTP ${response.status}）`;
  }
}
