import { describe, expect, it } from 'vitest';
import { mcpErrorText, queryResultText, taskErrorText } from './ipLabels';

/**
 * IP 专家工作台的展示口径（specs/opportunity-ip-agent）：无命中、失败、未知分开说，
 * 大模型的错误不能说成智慧芽的错误，任何一种都不能读成「低风险」或「可上架」。
 */
describe('ipLabels', () => {
  it('检索无命中只说本次范围内未找到', () => {
    const none = queryResultText({ status: 'success', total_count: 0, returned_count: 0, error_category: null });
    expect(none).toBe('本次检索范围内未找到');
    expect(none).not.toMatch(/低风险|可上架|安全/);
    expect(queryResultText({ status: 'success', total_count: 120, returned_count: 20, error_category: null })).toBe(
      '来源共 120 条，已读取 20 条',
    );
  });

  it('失败与未知给出原因，语法错误提示修改检索式', () => {
    expect(queryResultText({ status: 'failed', total_count: null, returned_count: null, error_category: 'bad_request' }))
      .toContain('检索式');
    expect(queryResultText({ status: 'unknown', total_count: null, returned_count: null, error_category: 'timeout' }))
      .toContain('可能已计费');
  });

  it('计划与分析的错误说成大模型，检索与取证说成智慧芽', () => {
    expect(taskErrorText('plan', 'credential_invalid')).toContain('大模型');
    expect(taskErrorText('analyze', 'invalid_output')).toContain('大模型');
    expect(taskErrorText('search', 'credential_invalid')).toContain('智慧芽');
    expect(taskErrorText('detail', null)).toBe('');
    expect(mcpErrorText('no_permission')).toContain('权限');
  });
});
