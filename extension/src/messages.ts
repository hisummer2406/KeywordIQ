// content script ↔ background 之间的消息形状。集中在一处，两边都从这里 import，
// 避免两份手写的形状悄悄漂移。

import type { ParsedItem } from './parse-storefront';

export interface CaptureMessage {
  type: 'CAPTURE_REPORT';
  sellerId: string;
  site: string;
  items: ParsedItem[];
  // content script 冻结（capture-accumulator.ts 的 freezeRequest），background
  // 直接使用、不再自己生成——否则同一批未变化的条目在提交失败后手动重新点
  // "提交上报"会被当成另一次全新观测，绕开幂等保护（PR #68 二次复审 P1）。
  requestId: string;
  capturedAt: string;
  pageContext?: string;
}

export type CaptureResult =
  | {
      outcome: 'success';
      accepted: number;
      newAsins: number;
      baselineEstablished: boolean;
      duplicate: boolean;
    }
  | { outcome: 'auth_error' }
  | { outcome: 'rejected'; message: string }
  | { outcome: 'idempotency_conflict'; message: string }
  | { outcome: 'network_error' }
  | { outcome: 'missing_token' }
  | { outcome: 'missing_base_url' };
