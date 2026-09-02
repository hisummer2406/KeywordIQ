import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  accessState,
  addItems,
  createGroup,
  exportAnalysis,
  getBrands,
  getCategories,
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
  getBrands,
  exportAnalysis,
}));
vi.mock('@/services/upload', () => ({ getSites }));
vi.mock('@/services/library', () => ({ listGroups, createGroup, addItems }));

import { clearSnapshot } from '@/pages/Analysis/stateCache';
import { applyFilters, renderSettled, settle, submit } from './__testing__/analysisHarness';

/**
 * 作用域三元组的解析、显式触发查询、切站点/日期的联动，以及检索框的目标判定。
 *
 * 分析屏的用例按主题分成四个文件（本文件是其中之一），**不是按体量硬切**：这一屏的
 * 用例曾集中在一个文件里跑 42.7 秒，而 vitest 按文件并行，一个文件就成了整个套件的
 * 关键路径（当时套件总耗时约 49 秒）。拆开之后各文件可以并行跑。
 *
 * 共用的替身与默认数据在 `__testing__/analysisMocks`，渲染帮手在
 * `__testing__/analysisHarness`。**每个文件都要自己调 `seedAnalysisMocks()`
 * 与 `clearSnapshot()`**——模块级快照与 localStorage 都跨用例存活。
 */
describe('分析查询屏 · 作用域与筛选', () => {
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

  it('竞对新品开关只改草稿，点开始筛选才带上 competitor_new（issue #56 P2）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: '竞对新品' }));
    await settle();
    // 与其余筛选同一条纪律：改草稿不发请求
    expect(searchAnalysis).toHaveBeenCalledTimes(1);

    await act(async () => {
      await user.click(applyFilters());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0]).toMatchObject({ competitor_new: true });
  });

  it('没有 competitor:view 权限时不显示竞对新品开关（PR #66 复审 P1）', async () => {
    // 这个开关读取的是竞对监控域的私有数据，只有 analysis:view 不足以授权；
    // 后端 /search、/export 在 competitor_new=true 时会再校验一次 competitor:view，
    // 这里只验证前端不提供这个入口。
    accessState.canViewCompetitor = false;
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

    expect(screen.queryByRole('switch', { name: '竞对新品' })).not.toBeInTheDocument();
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

    // 第三个参数是 { skipErrorHandler }，只在首屏按保存的站点乐观请求时才为真，
    // 这里不关心它的取值
    await waitFor(() => expect(getScope).toHaveBeenCalledWith('DE', 'DAY', expect.anything()));
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
});
