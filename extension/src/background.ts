// service worker：唯一发起网络请求的地方（design.md 决策 6）。收到 content
// script 的采集结果后，在这里冻结 request_id/captured_at（决策 3）、组装请求体、
// 发送并分类结果。

import { getApiBaseUrl, getAuthToken } from './auth';
import { buildReportRequest } from './build-report';
import type { CaptureMessage, CaptureResult } from './messages';
import { sendReport } from './report-client';

chrome.runtime.onMessage.addListener((message: CaptureMessage, _sender, sendResponse) => {
  if (message?.type !== 'CAPTURE_REPORT') return undefined;

  handleCapture(message).then(sendResponse);
  return true; // 告知 Chrome 这是异步响应，保持消息通道打开
});

export async function handleCapture(message: CaptureMessage): Promise<CaptureResult> {
  const token = await getAuthToken();
  if (!token) return { outcome: 'missing_token' };

  const baseUrl = await getApiBaseUrl();
  if (!baseUrl) return { outcome: 'missing_base_url' };

  // request_id 与 capturedAt 在此生成一次并通过 buildReportRequest 固定进请求体；
  // sendReport 内部的所有自动重试都复用这同一份序列化结果，不会重新生成。
  const body = buildReportRequest({
    sellerId: message.sellerId,
    site: message.site,
    items: message.items,
    requestId: crypto.randomUUID(),
    capturedAt: new Date().toISOString(),
    pageContext: message.pageContext,
  });

  const outcome = await sendReport(baseUrl, token, body);

  switch (outcome.kind) {
    case 'success':
      return {
        outcome: 'success',
        accepted: outcome.response.accepted,
        newAsins: outcome.response.new_asins,
        baselineEstablished: outcome.response.baseline_established,
        duplicate: outcome.response.duplicate_of !== null,
      };
    case 'auth_error':
      return { outcome: 'auth_error' };
    case 'rejected':
      return { outcome: 'rejected', message: outcome.message };
    case 'idempotency_conflict':
      return { outcome: 'idempotency_conflict', message: outcome.message };
    case 'network_error':
      return { outcome: 'network_error' };
  }
}
