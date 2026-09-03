// service worker：唯一发起网络请求的地方（design.md 决策 6）。收到 content
// script 的采集结果后，组装请求体、发送并分类结果。

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

  // requestId/capturedAt 由 content script 冻结后随消息传入（decision 3 +
  // capture-accumulator.ts 的 freezeRequest），background 不再自己生成——它是
  // 无状态的 service worker，每次消息都当作独立事件处理，没有能力判断"这是
  // 同一批条目的重新提交"（PR #68 二次复审 P1）。sendReport 内部的所有自动
  // 重试都复用这同一份序列化结果。
  const body = buildReportRequest({
    sellerId: message.sellerId,
    site: message.site,
    items: message.items,
    requestId: message.requestId,
    capturedAt: message.capturedAt,
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
