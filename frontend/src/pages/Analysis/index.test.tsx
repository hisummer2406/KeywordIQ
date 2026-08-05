import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { accessState, searchAnalysis, getScope, getCategories, getBrands, getSites, exportAnalysis } =
  vi.hoisted(() => ({
    accessState: { canExport: true },
    searchAnalysis: vi.fn(),
    getScope: vi.fn(),
    getCategories: vi.fn(),
    getBrands: vi.fn(),
    getSites: vi.fn(),
    exportAnalysis: vi.fn(),
  }));

vi.mock('@umijs/max', () => ({ useAccess: () => accessState }));
vi.mock('@/services/analysis', () => ({ searchAnalysis, getScope, getCategories, getBrands, exportAnalysis }));
vi.mock('@/services/upload', () => ({ getSites }));

import AnalysisPage from '@/pages/Analysis';

/** 渲染并把进屏时的异步 effect（作用域解析 → 表格首次请求）跑完，避免 act 警告淹没真实失败。 */
async function renderSettled(expandFilters = true) {
  await act(async () => {
    render(<AnalysisPage />);
  });
  if (expandFilters) {
    fireEvent.click(screen.getByRole('button', { name: /展开过滤条件/ }));
  }
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
const applyFilters = () => screen.getByRole('button', { name: /开始筛选/ });

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
  trend_dates: ['2026-07-14', '2026-07-15', '2026-07-16', '2026-07-17', '2026-07-18'],
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
    getBrands.mockResolvedValue([
      { brand: 'ACME', count: 9 },
      { brand: 'Anker', count: 4 },
    ]);
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

  it('过滤条件默认收起', async () => {
    await renderSettled(false);

    expect(screen.getByRole('button', { name: /展开过滤条件/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /开始筛选/ })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('搜索类目')).not.toBeInTheDocument();
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

    expect(screen.queryByText(/未生效条件/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '1~100' }));
    expect(screen.getByText(/未生效条件/)).toBeInTheDocument();

    await act(async () => {
      await user.click(applyFilters());
    });
    await waitFor(() => expect(screen.queryByText(/未生效条件/)).not.toBeInTheDocument());
  });

  it('点开始筛选发一次，且带上多选类目与区间条件', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await user.click(screen.getByRole('checkbox', { name: /Home/ }));
    await user.click(screen.getByRole('button', { name: '1~100' }));
    await act(async () => {
      await user.click(applyFilters());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    const body = searchAnalysis.mock.calls[1][0];
    expect(body.categories).toEqual(expect.arrayContaining(['Electronics', 'Home']));
    expect(body).toMatchObject({
      ranking_min: 1,
      ranking_max: 100,
      site: 'US',
      date: '2026-07-18',
    });
  });

  it('顶部立即查询不会偷带尚未应用的筛选条件', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: '1~100' }));
    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await act(async () => {
      await user.click(submit());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0].ranking_min).toBeUndefined();
    expect(searchAnalysis.mock.calls[1][0].categories).toBeUndefined();
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
    // 带上新站点的最新日期：类目计数按 (站点, 周期, 日期) 分片，与筛选同粒度。
    // 少这个日期就会取到别的日期的计数，用户对不上「下拉说多少 / 筛出来多少」。
    expect(getCategories).toHaveBeenCalledWith('DE', 'DAY', '2026-07-16');
    await settle();
    // 表格仍是上一份数据，查询要等用户点「立即查询」
    expect(searchAnalysis).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/未生效条件/)).toBeInTheDocument();
  });

  it('只换日期也要重拉类目计数（计数按日期分片），但不重查表格', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    getCategories.mockClear();
    const user = userEvent.setup();

    // 作用域栏的日期选择器：从默认的 2026-07-18 换到 2026-07-17
    await user.click(screen.getByText('2026年07月18日'));
    await act(async () => {
      await user.click(await screen.findByTitle('2026年07月17日'));
    });

    await waitFor(() => expect(getCategories).toHaveBeenCalledWith('US', 'DAY', '2026-07-17'));
    await settle();
    // 类目清单换了，但表格没重查——查询依旧只由「立即查询」触发
    expect(searchAnalysis).toHaveBeenCalledTimes(1);
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

  it('切站点后的查询不再带旧站点的类目（界面清了，生效条件也要清）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    // 勾一个类目并让它生效
    await user.click(screen.getByRole('checkbox', { name: /Electronics/ }));
    await act(async () => {
      await user.click(applyFilters());
    });
    await waitFor(() =>
      expect(searchAnalysis).toHaveBeenLastCalledWith(
        expect.objectContaining({ categories: ['Electronics'] }),
      ),
    );

    // 切到 DE：界面上的勾选被清掉
    await user.click(screen.getByText('🇺🇸 美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('🇩🇪 德国站（DE）'));
    });
    expect(screen.getByRole('checkbox', { name: /Electronics/ })).not.toBeChecked();

    // 生效条件里也不能留着它：否则表格查不出数据，而原因在界面上完全不可见
    await act(async () => {
      fireEvent.click(submit());
    });
    await settle();

    const body = searchAnalysis.mock.calls.at(-1)![0];
    expect(body.site).toBe('DE');
    expect(body.categories).toBeUndefined();
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
      await user.click(screen.getByTitle('2'));
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
      await user.click(screen.getByTitle('2'));
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

  it('作用域无可用日期时不发查询（避免查一个空日期）', async () => {
    getScope.mockResolvedValue({ site: 'US', view: 'DAY', latest_date: null, available_dates: [] });
    await renderSettled();

    await waitFor(() => expect(getScope).toHaveBeenCalled());
    await settle();
    expect(searchAnalysis).not.toHaveBeenCalled();
  });
});
