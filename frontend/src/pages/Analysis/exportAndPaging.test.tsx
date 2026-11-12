import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  ROW,
  accessState,
  addItems,
  createGroup,
  exportAnalysis,
  getBrands,
  getCategories,
  getFirstListed,
  getScope,
  getSites,
  listGroups,
  searchAnalysis,
  seedAnalysisMocks,
  userState,
} from './__testing__/analysisMocks';

vi.mock('@umijs/max', () => ({
  useAccess: () => accessState,
  useModel: () => ({ initialState: userState }),
}));
vi.mock('@/services/analysis', () => ({
  searchAnalysis,
  getScope,
  getCategories,
  getFirstListed,
  getBrands,
  exportAnalysis,
}));
vi.mock('@/services/upload', () => ({ getSites }));
vi.mock('@/services/library', () => ({ listGroups, createGroup, addItems }));

import { clearSnapshot } from '@/pages/Analysis/stateCache';
import { applyFilters, renderSettled } from './__testing__/analysisHarness';

/**
 * 结果总数何时实算、翻页如何沿用缓存，以及导出用的是生效条件而非草稿。
 *
 * 分析屏的用例按主题分成四个文件（本文件是其中之一），**不是按体量硬切**：这一屏的
 * 用例曾集中在一个文件里跑 42.7 秒，而 vitest 按文件并行，一个文件就成了整个套件的
 * 关键路径（当时套件总耗时约 49 秒）。拆开之后各文件可以并行跑。
 *
 * 共用的替身与默认数据在 `__testing__/analysisMocks`，渲染帮手在
 * `__testing__/analysisHarness`。**每个文件都要自己调 `seedAnalysisMocks()`
 * 与 `clearSnapshot()`**——模块级快照与 localStorage 都跨用例存活。
 */
describe('分析查询屏 · 工具栏、总数与导出', () => {
  beforeEach(() => {
    seedAnalysisMocks();
    // 会话缓存是模块级的，测试之间会串。不清的话第一个用例留下的快照会让后面每个
    // 用例的挂载都被当成「切走再切回」——初始化整段跳过，断言看到的是一屏空数据，
    // 而报错信息（找不到某个按钮）完全指不到这个原因。
    clearSnapshot();
    // 保存的默认条件在 localStorage 里，同样是跨用例存活的。不清的话前一个用例存的
    // 那组会成为后面每个用例的初始条件，症状与快照没清一模一样、同样指不到原因。
    window.localStorage.clear();
  });

  it('过滤区不重复提供关键词与 ASIN 输入（一个维度只有一个入口）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

    const panel = screen.getByText('搜索结果过滤').closest('.ant-card') as HTMLElement;
    expect(within(panel).queryByPlaceholderText(/ASIN/)).not.toBeInTheDocument();
    expect(within(panel).queryByPlaceholderText(/关键词/)).not.toBeInTheDocument();
  });

  it('过滤区标题分别对应左右两栏', async () => {
    await renderSettled();

    const categoryTitle = screen.getByText('选择筛选类目');
    const resultTitle = screen.getByText('搜索结果过滤');

    expect(categoryTitle.closest('.ant-col')).toHaveStyle({ flex: '0 0 260px' });
    expect(resultTitle.closest('.ant-col')).toHaveStyle({ flex: '1 1 auto' });
  });

  it('无 analysis:export 权限时不显示导出按钮', async () => {
    accessState.canExport = false;
    await renderSettled();

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /导出 CSV/ })).not.toBeInTheDocument();
  });

  it('表格工具栏左侧放操作与结果数，右侧放排序控件', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

    const toolbar = screen.getByText(/搜索结果：/).closest('.ant-pro-table-list-toolbar');
    const left = toolbar?.querySelector('.ant-pro-table-list-toolbar-left') as HTMLElement;
    const right = toolbar?.querySelector('.ant-pro-table-list-toolbar-right') as HTMLElement;

    expect(within(left).getByRole('button', { name: /导出 CSV/ })).toBeInTheDocument();
    expect(within(left).getByText('1')).toBeInTheDocument();
    expect(within(right).getByText('当前排名')).toBeInTheDocument();
    expect(within(right).getByRole('button', { name: /确.*定/ })).toBeInTheDocument();
  });

  /** 让结果集跨多页，才有"翻页"可点（默认夹具只有 1 行 = 1 页）。 */
  function seedMultiPage(total = 120) {
    searchAnalysis.mockResolvedValue({
      success: true, data: [ROW], total, site: 'US', view: 'DAY', date: '2026-07-18',
    });
  }

  it('条件变更才请求总数，翻页沿用缓存值', async () => {
    seedMultiPage();
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    // 首屏是一次条件变更，必须带 include_total
    expect(searchAnalysis.mock.calls[0][0].include_total).toBe(true);

    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await act(async () => {
      await user.click(applyFilters());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    // 改了筛选条件：命中行数会变，必须重算
    expect(searchAnalysis.mock.calls[1][0].include_total).toBe(true);

    // 翻页：命中行数不会变，不该再付一次精确计数的代价
    await act(async () => {
      // antd 6 的页码 `li` 只带 title，点击事件在它里面的 `<a role="button">` 上
      await user.click(within(screen.getByTitle('2')).getByRole('button'));
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));
    expect(searchAnalysis.mock.calls[2][0].include_total).toBe(false);
    expect(searchAnalysis.mock.calls[2][0].page).toBe(2);
  });

  it('翻页时后端返回 total=null，表格沿用上一次的总数（不清零）', async () => {
    seedMultiPage();
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    expect(screen.getByText('120')).toBeInTheDocument();
    const user = userEvent.setup();

    // 第二页按真实契约返回 total:null —— 这正是 include_total:false 的响应形状。
    // 若把 null 当成 0，结果数与翻页器会突然清空，而界面上看不出原因。
    searchAnalysis.mockResolvedValueOnce({
      success: true, data: [ROW], total: null, site: 'US', view: 'DAY', date: '2026-07-18',
    });
    await act(async () => {
      // antd 6 的页码 `li` 只带 title，点击事件在它里面的 `<a role="button">` 上
      await user.click(within(screen.getByTitle('2')).getByRole('button'));
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    // 结果数仍是首次算出的 120，没有被 null 冲成 0
    expect(screen.getByText('120')).toBeInTheDocument();
  });

  it('导出用当下生效的条件，且不带分页参数', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await act(async () => {
      await user.click(applyFilters());
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

  it('导出带上生效的排序（导出的就是屏幕上那份，行序也一样）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    // 工具栏改排序：按点击份额降序，点「确定」生效
    await user.click(screen.getByTitle('当前排名'));
    await act(async () => {
      await user.click(await screen.findByTitle('点击份额'));
    });
    await user.click(screen.getByTitle('升序'));
    await act(async () => {
      await user.click(await screen.findByTitle('降序'));
    });
    await act(async () => {
      // antd 会在两个汉字之间插空格，可访问名是「确 定」
      fireEvent.click(screen.getByRole('button', { name: /确\s*定/ }));
    });
    await waitFor(() =>
      expect(searchAnalysis).toHaveBeenLastCalledWith(
        expect.objectContaining({ orderBy: 'top1_click_share', orderDir: 'desc' }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );

    await act(async () => {
      await user.click(screen.getByRole('button', { name: /导出 CSV/ }));
    });

    await waitFor(() => expect(exportAnalysis).toHaveBeenCalled());
    expect(exportAnalysis.mock.calls[0][0]).toMatchObject({
      orderBy: 'top1_click_share',
      orderDir: 'desc',
    });
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

});
