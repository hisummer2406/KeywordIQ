import { errorText as vendorErrorText } from '@/pages/IP/riskLabels';
import type { DetailTool, IpTaskKind, QueryStatus, Relevance } from '@/services/opportunityIp';

/**
 * IP 专家工作台的展示文案。原则同 #24：失败、未知、未检索与「无命中」分开说，任何一种都
 * 不能读成「低风险」或「可上架」；Agent 分析不是法律意见（specs/opportunity-ip-agent）。
 */

export const TASK_KIND: Record<IpTaskKind, string> = {
  plan: '生成检索方案',
  search: '执行检索',
  detail: '取证',
  analyze: '生成风险分析',
  ask: '回答追问',
};

export const QUERY_STATUS: Record<QueryStatus, { text: string; color: string }> = {
  success: { text: '已执行', color: 'success' },
  failed: { text: '失败', color: 'error' },
  unknown: { text: '结果未知', color: 'warning' },
};

export const TOOL_TEXT: Record<DetailTool, string> = {
  get_patent_legal_status: '法律状态',
  bibliography: '著录',
  abstract_image: '摘要附图',
};

export const RELEVANCE: Record<Relevance, { text: string; color: string }> = {
  high: { text: '高相关', color: 'red' },
  medium: { text: '中相关', color: 'orange' },
  low: { text: '低相关', color: 'default' },
  unclear: { text: '无法判断', color: 'default' },
};

const LLM_ERROR: Record<string, string> = {
  not_configured: '大模型未配置，请联系管理员',
  credential_invalid: '大模型 Key 无效，请联系管理员',
  no_permission: '大模型 Key 没有权限',
  insufficient_balance: '大模型账户余额不足',
  rate_limited: '大模型调用过于频繁，请稍后重试',
  bad_request: '大模型拒绝了请求',
  invalid_output: '大模型返回的内容不符合要求，可以重试',
  client_error: '服务端未能发起请求（未调用大模型）',
  connect_failed: '无法连接大模型（请求未发出）',
  timeout: '大模型调用超时：结果未知，可能已计费',
  network: '网络中断：结果未知，可能已计费',
  upstream_5xx: '大模型服务异常：结果未知，可能已计费',
  malformed_response: '大模型返回内容无法解析：结果未知，可能已计费',
  interrupted: '执行中断：结果未知，可能已计费',
};

const MCP_OVERRIDE: Record<string, string> = {
  bad_request: '检索式不合法或参数被智慧芽拒绝，请修改后重试',
};

export function mcpErrorText(category: string | null | undefined): string {
  if (!category) return '';
  return MCP_OVERRIDE[category] ?? vendorErrorText(category);
}

/** 按任务种类给出中文提示：计划与分析是大模型，检索与取证是智慧芽 MCP。 */
export function taskErrorText(kind: IpTaskKind, category: string | null | undefined): string {
  if (!category) return '';
  if (kind === 'plan' || kind === 'analyze' || kind === 'ask') return LLM_ERROR[category] ?? '大模型调用失败';
  return mcpErrorText(category);
}

/** 检索式的执行结果说明：把「本次范围内未找到」与失败、未知分开。 */
export function queryResultText(q: { status: QueryStatus; total_count: number | null; returned_count: number | null; error_category: string | null }): string {
  if (q.status === 'success') {
    if (!q.total_count) return '本次检索范围内未找到';
    return `来源共 ${q.total_count} 条，已读取 ${q.returned_count ?? 0} 条`;
  }
  return mcpErrorText(q.error_category);
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export const WEEKDAY_OPTIONS = WEEKDAYS.map((label, value) => ({ value, label }));

/** 定时复检的频率说明，例如「每周一 09:00」「每月 15 日 09:00」（Asia/Shanghai）。 */
export function scheduleText(s: { frequency: 'weekly' | 'monthly'; weekday: number | null; day_of_month: number | null; hour: number }): string {
  const hh = `${String(s.hour).padStart(2, '0')}:00`;
  if (s.frequency === 'weekly') return `每${WEEKDAYS[s.weekday ?? 0]} ${hh}`;
  return `每月 ${s.day_of_month ?? 1} 日 ${hh}`;
}
