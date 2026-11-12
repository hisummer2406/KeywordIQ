import { describe, expect, it } from 'vitest';
import {
  archiveErrorText,
  coverageText,
  DECISIONS_NEED_SEARCH,
  detailBatchLimit,
  detailCallText,
  errorText,
  safeHttps,
  ymd,
} from './riskLabels';

/**
 * 外观专利核查的展示口径（specs/patent-risk-check、patent-risk-review）：无命中、未读取、
 * 失败与未知要分开说，任何一种都不能读成「低风险」或「可上架」。
 */
describe('riskLabels', () => {
  it('读取范围如实显示，无命中只说本次范围内未找到', () => {
    expect(coverageText(20, 100)).toBe('已读取 20 / 来源匹配 100');
    expect(coverageText(0, null)).toBe('尚未取得检索结果');
    const none = coverageText(0, 0);
    expect(none).toContain('未找到');
    expect(none).not.toMatch(/低风险|可上架|安全/);
  });

  it('结果未知的分类都提示可能已计费', () => {
    for (const c of ['timeout', 'network', 'upstream_5xx', 'malformed_response', 'interrupted']) {
      expect(errorText(c)).toContain('可能已计费');
    }
    expect(errorText('credential_invalid')).not.toContain('登录');
    expect(errorText('whatever')).toBe('调用失败');
    expect(errorText(null)).toBe('');
  });

  it('只放行 https 附图地址', () => {
    expect(safeHttps('https://img/a.png')).toBe('https://img/a.png');
    expect(safeHttps('javascript:alert(1)')).toBeUndefined();
    expect(safeHttps('http://img/a.png')).toBeUndefined();
    expect(safeHttps(null)).toBeUndefined();
  });

  it('检索未完成不能选可上架和条件通过', () => {
    expect(DECISIONS_NEED_SEARCH.sort()).toEqual(['conditional_pass', 'pass']);
  });

  it('供应商 yyyyMMdd 日期格式化', () => {
    expect(ymd(20240131)).toBe('2024-01-31');
    expect(ymd(null)).toBe('—');
  });

  it('含附图或公报时批量上限收紧到文件上限', () => {
    expect(detailBatchLimit(['bibliography', 'legal'], 20, 10)).toBe(20);
    expect(detailBatchLimit(['bibliography', 'drawings'], 20, 10)).toBe(10);
    expect(detailBatchLimit(['pdf'], 20, 10)).toBe(10);
  });

  it('调用说明列出每个接口的次数，不编造金额', () => {
    const text = detailCallText({ bibliography: 3, drawings: 2, pdf: 2 });
    expect(text).toContain('P011 著录 1 次（3 件');
    expect(text).toContain('P042 全部附图至少 2 次');
    expect(text).toContain('P020 公报 PDF 2 次');
    expect(text).not.toContain('P013');
    expect(text).not.toMatch(/元|¥/);
    expect(detailCallText({})).toContain('不会产生调用');
  });

  it('未存档原因有中文说明', () => {
    expect(archiveErrorText('archive_disabled')).toBe('对象存储未启用');
    expect(archiveErrorText('x')).toBe('未存档');
    expect(archiveErrorText(null)).toBe('');
  });
});
