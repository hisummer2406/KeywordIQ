import type { Decision, DetailStatus, RiskLevel, TaskStatus } from '@/services/risk';

/**
 * 外观专利核查的展示文案。原则：失败、未知、未检索与「无命中」分开说，任何一种都不能读成
 * 「低风险」或「可上架」（specs/patent-risk-check、patent-risk-review）。
 */

export const TASK_STATUS: Record<TaskStatus, { text: string; color: string }> = {
  queued: { text: '排队中', color: 'default' },
  running: { text: '检索中', color: 'processing' },
  completed: { text: '已完成', color: 'success' },
  failed: { text: '失败', color: 'error' },
  unknown: { text: '结果未知', color: 'warning' },
};

export const DETAIL_STATUS: Record<DetailStatus, { text: string; color: string }> = {
  success: { text: '已获取', color: 'success' },
  failed: { text: '失败', color: 'error' },
  unknown: { text: '结果未知', color: 'warning' },
  missing: { text: '来源无此项', color: 'default' },
};

export const RISK_LEVEL: Record<RiskLevel, { text: string; color: string }> = {
  high: { text: '高', color: 'red' },
  medium: { text: '中', color: 'orange' },
  low: { text: '低', color: 'green' },
  undetermined: { text: '无法判断', color: 'default' },
};

export const DECISION: Record<Decision, { text: string; color: string }> = {
  pass: { text: '可上架', color: 'green' },
  conditional_pass: { text: '条件通过', color: 'cyan' },
  continue_verify: { text: '继续验证', color: 'blue' },
  need_evidence: { text: '需补证', color: 'gold' },
  not_recommended: { text: '不建议上架', color: 'red' },
};

/** 检索未完成时不能选的建议（与后端 add_review 校验一致）。 */
export const DECISIONS_NEED_SEARCH: Decision[] = ['pass', 'conditional_pass'];

/** 后端错误分类 → 中文提示。供应商原始 error_msg 不会下发到前端。 */
const ERROR_CATEGORY: Record<string, string> = {
  not_configured: '智慧芽接口未配置，请联系管理员',
  credential_invalid: '智慧芽 Key 无效或已停用，请联系管理员',
  no_permission: '该 Key 没有此接口的权限，请联系管理员开通',
  insufficient_balance: '智慧芽余额或调用次数不足',
  client_expired: '智慧芽账户已过期',
  quota_exceeded: '今日调用额度已用完',
  rate_limited: '调用过于频繁，请稍后重试',
  bad_request: '请求被智慧芽拒绝（参数或图片不符合要求）',
  upstream_error: '智慧芽服务异常',
  unexpected_code: '智慧芽返回了未识别的错误',
  timeout: '调用超时：结果未知，可能已计费',
  network: '网络中断：结果未知，可能已计费',
  upstream_5xx: '智慧芽服务异常：结果未知，可能已计费',
  malformed_response: '返回内容不完整：结果未知，可能已计费',
  interrupted: '执行中断：结果未知，可能已计费',
};

export function errorText(category: string | null | undefined): string {
  if (!category) return '';
  return ERROR_CATEGORY[category] ?? '调用失败';
}

/** 供应商日期是 yyyyMMdd 整数。 */
export function ymd(v: number | null | undefined): string {
  if (!v) return '—';
  const s = String(v);
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : s;
}

export function dt(v: string | null | undefined): string {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 只放行 https 地址；javascript:、http: 等一律不渲染。 */
export function safeHttps(url: string | null | undefined): string | undefined {
  return typeof url === 'string' && url.startsWith('https://') ? url : undefined;
}

/** 读取范围：已读取 N / 来源匹配 M。未读取的部分不是「已排除」。 */
export function coverageText(fetched: number, total: number | null): string {
  if (total === null) return '尚未取得检索结果';
  if (total === 0) return '本次检索范围（所选法域）内未找到相似外观专利';
  return `已读取 ${Math.min(fetched, total)} / 来源匹配 ${total}`;
}
