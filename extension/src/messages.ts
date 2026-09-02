// content script ↔ background 之间的消息形状。集中在一处，两边都从这里 import，
// 避免两份手写的形状悄悄漂移。

import type { ParsedItem } from './parse-storefront';

export interface CaptureMessage {
  type: 'CAPTURE_REPORT';
  sellerId: string;
  site: string;
  items: ParsedItem[];
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
