import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { accessState, searchAnalysis, getScope, getCategories, getSites, exportAnalysis } = vi.hoisted(
  () => ({
    accessState: { canExport: true },
    searchAnalysis: vi.fn(),
    getScope: vi.fn(),
    getCategories: vi.fn(),
    getSites: vi.fn(),
    exportAnalysis: vi.fn(),
  }),
);

vi.mock('@umijs/max', () => ({ useAccess: () => accessState }));
vi.mock('@/services/analysis', () => ({ searchAnalysis, getScope, getCategories, exportAnalysis }));
vi.mock('@/services/upload', () => ({ getSites }));

import AnalysisPage from '@/pages/Analysis';

/** 渲染并把进屏时的异步 effect（作用域解析 → 表格首次请求）跑完，避免 act 警告淹没真实失败。 */
async function renderSettled() {
  await act(async () => {
    render(<AnalysisPage />);
  });
}

const ROW = {
  keyword_id: 1,
  keyword: 'coffee maker',
  current_ranking: 1,
  previous_ranking: 4,
  ranking_change: 3,
  is_new: false,
  top1_asin: 'B00TEST001',
  top1_title: 'Some Product',
  top1_brand: 'ACME',
  top1_category: 'Home',
  top1_click_share: 0.25,
  top1_conversion_share: 0.1,
  trend: [5, 4, 3, 2, 1],
  week_change: -7,
};

/**
 * 分析屏是核心屏。这里守的是作用域三元组能被解析并带进查询、表格渲染出行、
 * 以及导出按钮的权限门禁与"导出即当前筛选"这条约定。
 */
describe('分析查询屏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    accessState.canExport = true;
    getSites.mockResolvedValue([{ code: 'US', name_zh: '美国', domain: 'amazon.com' }]);
    getScope.mockResolvedValue({
      site: 'US',
      view: 'DAY',
      latest_date: '2026-07-18',
      available_dates: ['2026-07-18', '2026-07-17'],
    });
    getCategories.mockResolvedValue([{ category: 'Home', count: 12 }]);
    searchAnalysis.mockResolvedValue({
      success: true,
      data: [ROW],
      total: 1,
      site: 'US',
      view: 'DAY',
      date: '2026-07-18',
    });
    exportAnalysis.mockResolvedValue(undefined);
  });

  it('进屏解析作用域并把三元组带进查询', async () => {
    await renderSettled();

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
    expect(searchAnalysis.mock.calls[0][0]).toMatchObject({
      site: 'US',
      view: 'DAY',
      date: '2026-07-18',
    });
  });

  it('渲染出查询返回的行', async () => {
    await renderSettled();
    expect(await screen.findByText('coffee maker')).toBeInTheDocument();
    expect(screen.getByText('B00TEST001')).toBeInTheDocument();
  });

  it('无 analysis:export 权限时不显示导出按钮', async () => {
    accessState.canExport = false;
    await renderSettled();

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /导出 CSV/ })).not.toBeInTheDocument();
  });

  it('有权限时点击导出，用当前作用域与筛选、且不带分页参数', async () => {
    await renderSettled();
    const btn = await screen.findByRole('button', { name: /导出 CSV/ });

    await userEvent.setup().click(btn);

    await waitFor(() => expect(exportAnalysis).toHaveBeenCalled());
    const body = exportAnalysis.mock.calls[0][0];
    expect(body).toMatchObject({ site: 'US', view: 'DAY', date: '2026-07-18' });
    // 导出的是整个作用域，不是当前这一页
    expect(body).not.toHaveProperty('page');
    expect(body).not.toHaveProperty('perPage');
  });

  it('作用域无可用日期时不发查询（避免查一个空日期）', async () => {
    getScope.mockResolvedValue({ site: 'US', view: 'DAY', latest_date: null, available_dates: [] });
    await renderSettled();

    await waitFor(() => expect(getScope).toHaveBeenCalled());
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(searchAnalysis).not.toHaveBeenCalled();
  });
});
