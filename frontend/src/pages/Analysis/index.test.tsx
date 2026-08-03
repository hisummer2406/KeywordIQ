import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
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

/** 给"之后没有再发请求"这类断言一个观察窗口。 */
async function settle(ms = 60) {
  await act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, ms);
    });
  });
}

const submit = () => screen.getByRole('button', { name: /立即查询/ });

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
  top1_click_share: 25,
  top1_conversion_share: 10,
  trend: [5, 4, 3, 2, 1],
  week_change: -7,
};

/**
 * 分析屏是核心屏。这里守的是三件事：作用域三元组能被解析并带进查询、
 * **查询只在显式触发时发生**（改条件不发请求）、以及导出导的是当下生效的那份。
 */
describe('分析查询屏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    accessState.canExport = true;
    getSites.mockResolvedValue([
      { code: 'US', name_zh: '美国', domain: 'amazon.com' },
      { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
    ]);
    getScope.mockImplementation(async (site?: string) =>
      site === 'DE'
        ? { site: 'DE', view: 'DAY', latest_date: '2026-07-16', available_dates: ['2026-07-16'] }
        : {
            site: 'US',
            view: 'DAY',
            latest_date: '2026-07-18',
            available_dates: ['2026-07-18', '2026-07-17'],
          },
    );
    getCategories.mockResolvedValue([
      { category: 'Home', count: 12 },
      { category: 'Electronics', count: 7 },
    ]);
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

  it('进屏解析作用域并把三元组带进首次查询', async () => {
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

  it('改条件后不点查询就不发请求', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText(/输入 ASIN 或关键词/), 'coffee');
    await user.click(screen.getByRole('button', { name: '1~100' }));
    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await settle();

    // 首屏那一次之外，一次都不该多
    expect(searchAnalysis).toHaveBeenCalledTimes(1);
  });

  it('存在未生效的改动时给出提示，查询后提示消失', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    expect(screen.queryByText(/条件已修改/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '1~100' }));
    expect(screen.getByText(/条件已修改/)).toBeInTheDocument();

    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(screen.queryByText(/条件已修改/)).not.toBeInTheDocument());
  });

  it('点立即查询发一次，且带上多选类目与区间条件', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await user.click(screen.getByRole('checkbox', { name: /Home/ }));
    await user.click(screen.getByRole('button', { name: '1~100' }));
    await act(async () => {
      await user.click(submit());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    const body = searchAnalysis.mock.calls[1][0];
    expect(body.categories).toEqual(expect.arrayContaining(['Electronics', 'Home']));
    expect(body).toMatchObject({ ranking_min: 1, ranking_max: 100, site: 'US', date: '2026-07-18' });
  });

  it('检索框按输入格式判定检索目标并回显', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();
    const box = screen.getByPlaceholderText(/输入 ASIN 或关键词/);

    await user.type(box, 'B07Z82895W');
    expect(screen.getByText('按 ASIN 检索')).toBeInTheDocument();
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0]).toMatchObject({ asin: 'B07Z82895W' });
    expect(searchAnalysis.mock.calls[1][0].keyword).toBeUndefined();

    await user.clear(box);
    await user.type(box, 'coffee maker');
    expect(screen.getByText('按关键词检索')).toBeInTheDocument();
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));
    expect(searchAnalysis.mock.calls[2][0]).toMatchObject({ keyword: 'coffee maker' });
    expect(searchAnalysis.mock.calls[2][0].asin).toBeUndefined();
  });

  it('切站点重拉日期与类目，但不重查表格', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    getScope.mockClear();
    getCategories.mockClear();
    const user = userEvent.setup();

    await user.click(screen.getByText('🇺🇸 美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('🇩🇪 德国站（DE）'));
    });

    await waitFor(() => expect(getScope).toHaveBeenCalledWith('DE', 'DAY'));
    expect(getCategories).toHaveBeenCalledWith('DE', 'DAY');
    await settle();
    // 表格仍是上一份数据，查询要等用户点「立即查询」
    expect(searchAnalysis).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/条件已修改/)).toBeInTheDocument();
  });

  it('切站点清空已勾选的类目（旧站点的类目在新站点下可能不存在）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    const electronics = screen.getByRole('checkbox', { name: /Electronics/ });
    await user.click(electronics);
    expect(electronics).toBeChecked();

    await user.click(screen.getByText('🇺🇸 美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('🇩🇪 德国站（DE）'));
    });

    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /Electronics/ })).not.toBeChecked(),
    );
  });

  it('过滤区不重复提供关键词与 ASIN 输入（一个维度只有一个入口）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

    const panel = screen.getByText('搜索结果过滤').closest('.ant-card') as HTMLElement;
    expect(within(panel).queryByPlaceholderText(/ASIN/)).not.toBeInTheDocument();
    expect(within(panel).queryByPlaceholderText(/关键词/)).not.toBeInTheDocument();
  });

  it('无 analysis:export 权限时不显示导出按钮', async () => {
    accessState.canExport = false;
    await renderSettled();

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /导出 CSV/ })).not.toBeInTheDocument();
  });

  it('导出用当下生效的条件，且不带分页参数', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));

    await act(async () => {
      await user.click(screen.getByRole('button', { name: /导出 CSV/ }));
    });

    await waitFor(() => expect(exportAnalysis).toHaveBeenCalled());
    const body = exportAnalysis.mock.calls[0][0];
    expect(body).toMatchObject({ site: 'US', view: 'DAY', date: '2026-07-18' });
    expect(body.categories).toEqual(['Electronics']);
    // 导出的是整个作用域下的这组条件，不是当前这一页
    expect(body).not.toHaveProperty('page');
    expect(body).not.toHaveProperty('perPage');
  });

  it('导出用的是生效条件而非草稿（改了条件没查，导的仍是表格里那份）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: /Electronics/ })); // 只改草稿，不查
    await act(async () => {
      await user.click(screen.getByRole('button', { name: /导出 CSV/ }));
    });

    await waitFor(() => expect(exportAnalysis).toHaveBeenCalled());
    expect(exportAnalysis.mock.calls[0][0].categories).toBeUndefined();
  });

  it('作用域无可用日期时不发查询（避免查一个空日期）', async () => {
    getScope.mockResolvedValue({ site: 'US', view: 'DAY', latest_date: null, available_dates: [] });
    await renderSettled();

    await waitFor(() => expect(getScope).toHaveBeenCalled());
    await settle();
    expect(searchAnalysis).not.toHaveBeenCalled();
  });
});
