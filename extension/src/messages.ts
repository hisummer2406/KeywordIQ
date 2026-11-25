// content script ↔ background 之间的消息形状。集中在一处，两边都从这里 import，
// 避免两份手写的形状悄悄漂移。

import type { ParsedItem } from './parse-storefront';
import type { SelectorOverrides } from './selector-overrides';

/**
 * 自动采集（试点，#205 阶段 1）的控制消息：设置页发起运行或停止、通知设置变更，
 * content script 询问自己所在标签页是不是自动采集打开的（是就不渲染人工面板）。
 */
export type AutoControlMessage =
  | { type: 'AUTO_IS_AUTO_TAB' }
  | { type: 'AUTO_RUN_NOW' }
  | { type: 'AUTO_STOP' }
  | { type: 'AUTO_CONFIG_CHANGED' };

export type AutoControlResult = { ok: true; isAutoTab?: boolean } | { ok: false; reason?: string };

/** 后台请 content script 解析当前页（自动采集）。回答形状见 auto-plan.ts 的 PageScan。 */
export interface AutoScanRequest {
  type: 'AUTO_SCAN';
  /** 本轮开跑时快照的服务端选择器覆盖；没有就用内置。 */
  selectors?: SelectorOverrides;
}

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
  /** 页面上尽力解析出的卖家店名；取不到就不带（#166 D5），不拿 seller_id 之类拼凑。 */
  sellerName?: string;
}

/** 打开采集面板时读取服务端单批上限（#166 D4）。 */
export interface ConfigMessage {
  type: 'GET_COLLECTOR_CONFIG';
}

/**
 * `unavailable` 不细分成因（网络、服务端、未配置地址、令牌都同步不到）：面板对它们的
 * 处理完全一样——禁止开始采集并提示，运营去设置页或网页上处理即可。
 */
export type ConfigResult =
  | {
      outcome: 'ok';
      maxReportItems: number;
      /** 服务端下发的页面选择器覆盖（只有自动采集使用，人工面板忽略）。 */
      selectors?: SelectorOverrides;
      selectorsVersion?: string;
    }
  | { outcome: 'unavailable' };

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
  // 令牌同步失败的三种成因各自成一个结局，**不合并**：运营要做的事完全不同
  // （重新授权 / 打开网页 / 在网页上登录）。合并成一个就只能给出一句放之四海皆准
  // 的提示，而那句话在三种情况下都不足以让人知道下一步该干什么。
  | { outcome: 'sync_no_permission' }
  | { outcome: 'sync_no_tab' }
  | { outcome: 'sync_not_logged_in' }
  | { outcome: 'missing_base_url' };
