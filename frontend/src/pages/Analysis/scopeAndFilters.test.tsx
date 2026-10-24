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
  getFirstListed,
  getKeywordTrend,
  getScope,
  getSites,
  listGroups,
  ROW,
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
vi.mock('@/services/keywordTrend', () => ({ getKeywordTrend }));

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

    await user.type(screen.getByLabelText('搜索内容'), 'coffee');
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

  it('竞对新发现开关只改草稿，点开始筛选才带上 competitor_new（issue #56 P2）', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: '竞对新发现' }));
    await settle();
    // 与其余筛选同一条纪律：改草稿不发请求
    expect(searchAnalysis).toHaveBeenCalledTimes(1);

    await act(async () => {
      await user.click(applyFilters());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0]).toMatchObject({ competitor_new: true });
  });

  /**
   * `competitor_new` 曾漏出 `FILTER_KEYS`（PR #66 二次复审 P2）：只启用它、其余筛选
   * （包括默认的「只看首次入榜」）都关掉时，后端仍会走带筛选路径、排除全部掉榜行，但界面
   * 曾因为这个清单不完整而不显示既有的「本次结果不含掉榜行」提示——用户看到的会是
   * 一份悄悄漏了行的结果，且没有任何解释。
   */
  it('只启用竞对新发现筛选（首次入榜切到全部）时仍显示掉榜提示', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    // 关掉默认的首次入榜筛选，让「竞对新发现」成为生效条件里唯一在起作用的筛选维度。
    // 按 value 用 JS `===` 定位，不用 `[value="all"]` 属性选择器——jsdom 的属性匹配
    // 大小写不敏感，会连带选中行集合开关那组默认选中的 `ALL`。
    const allOption = Array.from(
      document.querySelectorAll<HTMLInputElement>('input[type="radio"]'),
    ).find((radio) => radio.value === 'all')!.closest('label')!;
    fireEvent.click(allOption);

    await user.click(screen.getByRole('switch', { name: '竞对新发现' }));
    await act(async () => {
      await user.click(applyFilters());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    const body = searchAnalysis.mock.calls[1][0];
    expect(body.competitor_new).toBe(true);
    expect(body.first_listed).toBeUndefined();
    expect(await screen.findByText(/本次结果不含掉榜行/)).toBeInTheDocument();
  });

  it('没有 competitor:view 权限时不显示竞对新发现开关（PR #66 复审 P1）', async () => {
    // 这个开关读取的是竞对监控域的私有数据，只有 analysis:view 不足以授权；
    // 后端 /search、/export 在 competitor_new=true 时会再校验一次 competitor:view，
    // 这里只验证前端不提供这个入口。
    accessState.canViewCompetitor = false;
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

    expect(screen.queryByRole('switch', { name: '竞对新发现' })).not.toBeInTheDocument();
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

  it('检索目标由所选搜索方式决定，切换后另一目标不残留', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();
    const box = screen.getByLabelText('搜索内容');
    const pickMode = async (label: string) => {
      fireEvent.mouseDown(document.querySelector('.scope-bar__mode .ant-select-selector')!);
      fireEvent.click(await screen.findByText(label, { selector: '.ant-select-item-option-content' }));
    };

    // 关键词方式下形如 ASIN 的输入仍按关键词查
    await user.type(box, 'B07Z82895W');
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0]).toMatchObject({ keyword: 'B07Z82895W', keyword_mode: 'ALL_TERMS' });
    expect(searchAnalysis.mock.calls[1][0].asins).toBeUndefined();

    await pickMode('ASIN · 批量');
    await user.clear(box);
    await user.type(box, 'b07z82895w, 3161484100{Enter}');
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));
    expect(searchAnalysis.mock.calls[2][0]).toMatchObject({ asins: ['B07Z82895W', '3161484100'] });
    expect(searchAnalysis.mock.calls[2][0].keyword).toBeUndefined();
    expect(searchAnalysis.mock.calls[2][0].keyword_mode).toBeUndefined();

    await pickMode('关键词 · 词组');
    await user.clear(box);
    await user.type(box, 'coffee maker');
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(4));
    expect(searchAnalysis.mock.calls[3][0]).toMatchObject({ keyword: 'coffee maker', keyword_mode: 'PHRASE' });
    expect(searchAnalysis.mock.calls[3][0].asins).toBeUndefined();
  });

  /**
   * 检索模式必须跟检索词一起固化（PR #129 复审 P1）：`submitScope` 曾只复制 keyword/asin，
   * 查过一次后切到「连续词组」再点立即查询，请求仍带着上一次的模式，且 `draftSig` 与生效
   * 签名永远对不上。
   */
  it('查询后切换检索模式再点立即查询，请求带新模式且不再显示未生效改动', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('搜索内容'), 'coffee maker');
    await act(async () => {
      await user.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    expect(searchAnalysis.mock.calls[1][0]).toMatchObject({ keyword_mode: 'ALL_TERMS' });

    fireEvent.mouseDown(document.querySelector('.scope-bar__mode .ant-select-selector')!);
    fireEvent.click(await screen.findByText('关键词 · 词组', { selector: '.ant-select-item-option-content' }));
    await act(async () => {
      await user.click(submit());
    });

    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));
    expect(searchAnalysis.mock.calls[2][0]).toMatchObject({
      keyword: 'coffee maker',
      keyword_mode: 'PHRASE',
    });
    expect(screen.queryByText(/条件已修改|有未生效/)).not.toBeInTheDocument();
  });

  it('切站点重拉日期与类目，但不重查表格', async () => {
    await renderSettled();
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    getScope.mockClear();
    getCategories.mockClear();
    const user = userEvent.setup();

    await user.click(screen.getByText('美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('德国站（DE）'));
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

    await user.click(screen.getByText('美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('德国站（DE）'));
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
    await user.click(screen.getByText('美国站（US）'));
    await act(async () => {
      await user.click(await screen.findByTitle('德国站（DE）'));
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

  describe('详情抽屉不能领先于表格实际加载的作用域', () => {
    // apply()（立即查询）把 appliedRef/appliedView 同步改到新作用域，随后才发一轮网络
    // 往返；这段窗口内表格仍显示旧作用域那批行。若点开这些行的详情，抽屉用的必须是这批
    // 行实际所属的站点/视图/日期，不是已经变了的 appliedRef/appliedView——否则会把旧行
    // 的 keyword_id 配上新站点，触发后端跨站点校验（422）（PR #87 复审 P2）。
    it('切站点并点立即查询后、网络往返还没完成时，点开的详情仍用当前显示的行所属的站点', async () => {
      getKeywordTrend.mockResolvedValue({
        success: true,
        keyword_id: 1,
        keyword: 'coffee maker',
        site: 'US',
        view: 'DAY',
        range: 'RECENT_30D',
        on: '2026-07-18',
        coverage: { start: '2026-07-12', end: '2026-07-18' },
        points: [{ date: '2026-07-18', status: 'RANKED', ranking: 1 }],
        best_ranking: { date: '2026-07-18', ranking: 1 },
        latest_available: { date: '2026-07-18', ranking: 1 },
      });

      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

      const user = userEvent.setup();
      await user.click(screen.getByText('美国站（US）'));
      await act(async () => {
        await user.click(await screen.findByTitle('德国站（DE）'));
      });

      // 卡住切到 DE 后「立即查询」触发的第二次 searchAnalysis，制造"appliedRef 已经是
      // DE、表格还没重新加载完成"的窗口
      let resolveSecond!: (value: Awaited<ReturnType<typeof searchAnalysis>>) => void;
      const secondCall = new Promise<Awaited<ReturnType<typeof searchAnalysis>>>((resolve) => {
        resolveSecond = resolve;
      });
      searchAnalysis.mockReturnValueOnce(secondCall);

      await act(async () => {
        fireEvent.click(submit());
      });
      // ProTable 的表格请求走一个约 20~30ms 的真实防抖（`useDebounceFn`），不是纯微任务；
      // `act` 只flush 微任务，不推进真实定时器。裸同步断言在这台机器空闲、`act` 之后
      // 又恰好被其他同步开销拖过防抖窗口时侥幸成立，负载稍高就先于定时器触发而翻红
      // （本地实测同一份代码不改动，独立跑与整文件一起跑失败率从 0 到约 75% 不等，
      // 与本轮改动无关，是这条断言写法本身对系统负载敏感）。改成 `waitFor` 消除这个
      // 与被测逻辑无关的计时竞争。
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));

      // 这段窗口内表格里仍是 US 的 coffee maker 那一行——点开它的详情
      await act(async () => {
        fireEvent.click(screen.getByTitle('查看关键词 ABA 排名趋势详情'));
      });

      // 抽屉必须按这批行实际所属的 US/DAY/2026-07-18 取数，不能用已经变成 DE 的 appliedRef
      await waitFor(() => expect(getKeywordTrend).toHaveBeenCalled());
      expect(getKeywordTrend.mock.calls[0][0]).toMatchObject({ site: 'US', on: '2026-07-18' });

      await act(async () => {
        resolveSecond({ success: true, data: [], total: 0, site: 'DE', view: 'DAY', date: '2026-07-16' });
      });

      // 表格随后真的切到了 DE（loadedSite 变了），但抽屉在打开那一刻已经把 US/DAY/
      // 2026-07-18 冻结进 trendTarget——不应因为 loadedSite 事后变化就改用新站点重新
      // 发请求（PR #87 复审第二轮 P2：第一版修复只护住了"点击当下读到的值"，没护住
      // "点击之后 loadedSite 才变化"这一步，抽屉的 site/view/on props 当时仍直接读活
      // 状态，会在这里触发第二次以 DE 发起的 getKeywordTrend）。
      await settle();
      expect(getKeywordTrend).toHaveBeenCalledTimes(1);
    });

    /**
     * ProTable 允许同一时刻有多个表格 `request` 调用同时在飞——旧调用不会被真的取消
     * （`useFetchData` 的 abort 只掐断它自己内部的 debounce race，不掐断已经发出的
     * `getData` promise 本身）。若一次更早发出的调用在一次更新的调用之后才落地，
     * `runRequest` 若不核对调用序号，仍会用这次旧响应把 `loadedSite` 改回旧站点，
     * 表格明明已经在展示新站点的行，之后点开的详情却按旧站点取数（PR #87 第五轮 P2）。
     */
    /**
     * 访问变色（issue #125，PR #127 复审 P2）：切站点后新请求还没落地时，屏上仍是旧站点的行。
     * 此刻点击关键词，访问记录必须记在行实际所属的站点（US）名下，不能记到已提交的新站点（DE）。
     */
    it('新站点请求在途时点击旧行关键词，访问记录记在旧行所属的站点', async () => {
      window.localStorage.clear();
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1)); // 首屏：US

      const user = userEvent.setup();
      searchAnalysis.mockReturnValueOnce(new Promise(() => {})); // DE 请求永不落地

      await user.click(screen.getByText('美国站（US）'));
      await act(async () => {
        await user.click(await screen.findByTitle('德国站（DE）'));
      });
      await act(async () => {
        fireEvent.click(submit());
      });
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));

      await act(async () => {
        fireEvent.click(screen.getByText('coffee maker').closest('a')!);
      });

      const stored = JSON.parse(window.localStorage.getItem('keywordiq.analysis.visited.1') ?? '{}');
      expect(stored.keys).toEqual(['US|coffee maker']);
    });

    it('切站点两次、较早那次请求较晚落地时，不倒灌覆盖较新请求的站点', async () => {
      getKeywordTrend.mockResolvedValue({
        success: true,
        keyword_id: 1,
        keyword: 'coffee maker',
        site: 'US',
        view: 'DAY',
        range: 'RECENT_30D',
        on: '2026-07-18',
        coverage: { start: '2026-07-12', end: '2026-07-18' },
        points: [{ date: '2026-07-18', status: 'RANKED', ranking: 1 }],
        best_ranking: { date: '2026-07-18', ranking: 1 },
        latest_available: { date: '2026-07-18', ranking: 1 },
      });

      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1)); // 首屏：US

      const user = userEvent.setup();

      // 切到 DE 并立即查询：这次请求（较早发出）先卡住，稍后才落地
      let resolveOlder!: (value: Awaited<ReturnType<typeof searchAnalysis>>) => void;
      const olderCall = new Promise<Awaited<ReturnType<typeof searchAnalysis>>>((resolve) => {
        resolveOlder = resolve;
      });
      searchAnalysis.mockReturnValueOnce(olderCall);

      await user.click(screen.getByText('美国站（US）'));
      await act(async () => {
        await user.click(await screen.findByTitle('德国站（DE）'));
      });
      await act(async () => {
        fireEvent.click(submit());
      });
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));

      // 再切回 US 并立即查询：这次（较新发出）用默认 mock，立即落地
      // 用 site 选择器自己范围内的当前选中值来开下拉——这是本文件唯一一处切换两次的
      // 用例：第一次切换后 rc-select 的下拉选项列表仍挂在 DOM 里（关闭态但未卸载），
      // 裸 `getByText('德国站（DE）')` 会同时命中"当前选中值"与那份残留列表项，
      // 报"找到多个元素"。
      const siteSelectValue = () =>
        document.querySelector('.scope-bar__site .ant-select-selection-item') as HTMLElement;
      await user.click(siteSelectValue());
      await act(async () => {
        await user.click(await screen.findByTitle('美国站（US）'));
      });
      await act(async () => {
        fireEvent.click(submit());
      });
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));

      // 较早那次（DE）现在才落地——响应里仍带同一行，只是站点标成 DE，用来隔离
      // ProTable 自身会不会采纳这份陈旧响应去渲染表格（不是本条要守的范围），
      // 单独验证 loadedSite 有没有被这次陈旧响应倒灌覆盖。
      await act(async () => {
        resolveOlder({ success: true, data: [ROW], total: 1, site: 'DE', view: 'DAY', date: '2026-07-16' });
      });
      await settle();

      await act(async () => {
        fireEvent.click(screen.getByTitle('查看关键词 ABA 排名趋势详情'));
      });

      // 必须仍是较新那次请求（US）的作用域，不能被较早却后落地的 DE 响应改回去
      await waitFor(() => expect(getKeywordTrend).toHaveBeenCalled());
      expect(getKeywordTrend.mock.calls[0][0]).toMatchObject({ site: 'US', on: '2026-07-18' });
    });
  });
});
