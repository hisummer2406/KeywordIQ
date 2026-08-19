import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
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
import { applyFilters, renderSettled, settle } from './__testing__/analysisHarness';

/**
 * 首屏自动查询、默认只看新词、以及用户保存的默认条件三级优先级。
 *
 * 分析屏的用例按主题分成四个文件（本文件是其中之一），**不是按体量硬切**：这一屏的
 * 用例曾集中在一个文件里跑 42.7 秒，而 vitest 按文件并行，一个文件就成了整个套件的
 * 关键路径（当时套件总耗时约 49 秒）。拆开之后各文件可以并行跑。
 *
 * 共用的替身与默认数据在 `__testing__/analysisMocks`，渲染帮手在
 * `__testing__/analysisHarness`。**每个文件都要自己调 `seedAnalysisMocks()`
 * 与 `clearSnapshot()`**——模块级快照与 localStorage 都跨用例存活。
 */
describe('分析查询屏 · 首屏与默认条件', () => {
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

  it('过滤条件默认收起', async () => {
    await renderSettled(false);

    expect(screen.getByRole('button', { name: /展开过滤条件/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /开始筛选/ })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('搜索类目')).not.toBeInTheDocument();
  });

  /**
   * 首屏按默认作用域自动查一次。这一屏是主力数据屏，用户进来就是要看数据的——延迟到
   * 点击才查的是持续增长市场屏，不是这一屏（曾经把两屏做反过一次）。
   */
  describe('首屏', () => {
    it('拉完作用域后自动查一次，条件是默认作用域', async () => {
      await renderSettled(false);

      await waitFor(() => expect(getScope).toHaveBeenCalled());
      expect(getSites).toHaveBeenCalled();
      expect(getCategories).toHaveBeenCalled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
      expect(searchAnalysis.mock.calls[0][0]).toMatchObject({
        site: 'US',
        view: 'DAY',
        date: '2026-07-18',
        // 默认筛选是「只看新词」——首屏回答的是「今天有哪些新词」
        is_new: true,
      });
    });

    it('自动查询之后条数就出现', async () => {
      await renderSettled(false);

      expect(await screen.findByText(/搜索结果：/)).toBeInTheDocument();
    });

    it('自动查询只发一次，不会连着查两遍', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
      await settle();

      expect(searchAnalysis).toHaveBeenCalledTimes(1);
    });

    it('首屏不提示「存在未生效条件」（一个条件都没改，那句话是错的）', async () => {
      await renderSettled(false);
      await settle();

      expect(screen.queryByText(/未生效条件/)).not.toBeInTheDocument();
    });

    /**
     * 站点一期数据都没有时，`request` 开头那道 `!body?.date` 守卫会挡掉请求。此时若沿用
     * 「搜索结果：0」，用户会去排查筛选条件，而真正的原因是这个站点根本没导过数据。
     */
    it('作用域没有可查日期时不发请求，空表格说明是没有数据', async () => {
      getScope.mockResolvedValue({
        site: 'US',
        view: 'DAY',
        latest_date: null,
        available_dates: [],
      });

      await renderSettled(false);
      await waitFor(() => expect(getScope).toHaveBeenCalled());
      await settle();

      expect(searchAnalysis).not.toHaveBeenCalled();
      expect(screen.getByText(/还没有导入任何数据/)).toBeInTheDocument();
      // 用「搜索结果：」带冒号的形式，否则会匹配到过滤区标题「搜索结果过滤」
      expect(screen.queryByText(/搜索结果：/)).not.toBeInTheDocument();
    });
  });

  /**
   * 默认筛选是「只看新词」——用户进这一屏最常问的第一个问题是「今天有哪些新词」。
   *
   * 三条边界都要守住：控件显示与实际发出的条件一致（否则用户以为系统漏筛了）、
   * 会话快照优先于默认值（用户主动关掉的筛选不该被默认值覆盖回去）、切视图不动筛选。
   */
  describe('默认只看新词', () => {
    /**
     * 新词那组按钮按 `value` 定位，不按可见文字：Row 的标签「新词」与选项「新词」文字
     * 相同，`getByText` 会撞上两个。
     *
     * **必须用 JS 的 `===` 比对，不能用 `[value="all"]` 属性选择器**：jsdom 里属性值
     * 匹配是大小写不敏感的，`[value="all"]` 会选中行集合开关那组的 `ALL`——而它默认就是
     * 选中态，断言「全部未被选中」于是失败，报错却完全指不到选错了控件。
     */
    const isNewOption = (value: 'all' | 'true' | 'false') =>
      Array.from(document.querySelectorAll<HTMLInputElement>('input[type="radio"]'))
        .find((radio) => radio.value === value)!
        .closest('label')!;

    /**
     * 选中与否看 antd 的 `-checked` 类名，**不看原生 `input.checked`**。
     *
     * 这一屏两组 Radio.Group（行集合 ALL/LISTED/DROPPED、新词 all/true/false）在 jsdom 里
     * 被当成**同一个原生 radio 组**——实测在检索框里打个字，行集合的 `ALL` 就自己变成
     * 未选中；点新词的「全部」反而让 `ALL` 回到选中、把新词的「新词」顶掉。原生 checked
     * 在这里根本不描述界面显示的是什么，而类名由 React props 渲染，才是真的。
     */
    const isNewChecked = (value: 'all' | 'true' | 'false') =>
      isNewOption(value).classList.contains('ant-radio-button-wrapper-checked');

    /** 点外层 label：input 被 antd 藏了，直接点它不会触发 Radio.Group 的 onChange。 */
    const clickIsNew = (value: 'all' | 'true' | 'false') =>
      fireEvent.click(isNewOption(value));

    const searchBox = () => screen.getByPlaceholderText(/输入 ASIN 或关键词/);

    it('首屏新词控件就显示为「新词」', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      expect(isNewChecked('true')).toBe(true);
      expect(isNewChecked('all')).toBe(false);
    });

    /**
     * 默认带筛选的直接后果：首屏就走带筛选路径，掉榜行不在结果内。`is_new` 在
     * `FILTER_KEYS` 里，所以那句提示自动成立——不额外加判断，也不能让它漏掉：
     * 用户看到的会是「进来就没有掉榜词」，而没有任何东西解释为什么。
     */
    it('首屏就提示本次结果不含掉榜行（默认带筛选的直接后果）', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      expect(await screen.findByText(/本次结果不含掉榜行/)).toBeInTheDocument();
    });

    it('「重置条件」回到默认（只看新词）而非清空，且不动检索框', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      fireEvent.change(searchBox(), { target: { value: 'coffee' } });
      clickIsNew('all');
      await waitFor(() => expect(isNewChecked('all')).toBe(true));

      fireEvent.click(screen.getByRole('button', { name: /重置条件/ }));

      await waitFor(() => expect(isNewChecked('true')).toBe(true));
      expect(searchBox()).toHaveValue('coffee');
    });

    it('用户关掉新词筛选后切走再切回，复原的是「全部」而不是默认值', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      clickIsNew('all');
      await act(async () => {
        fireEvent.click(applyFilters());
      });
      await waitFor(() => expect(searchAnalysis.mock.calls.at(-1)![0].is_new).toBeUndefined());

      cleanup();
      vi.clearAllMocks();

      await renderSettled(false); // 第二次挂载 = 切回来。快照连 filterOpen 一起复原，
      // 过滤区已是展开态，再点「展开过滤条件」会找不到按钮
      await settle();

      expect(searchAnalysis).not.toHaveBeenCalled(); // 快照那条路：一个请求都不发
      expect(isNewChecked('all')).toBe(true);
    });

    it('切到周视图时新词筛选原样保留', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      // 视图是 antd Select，得先开下拉再点选项
      await act(async () => {
        fireEvent.mouseDown(document.querySelector('.scope-bar__view .ant-select-selector')!);
      });
      await act(async () => {
        fireEvent.click(await screen.findByTitle('按周'));
      });
      await settle();

      expect(isNewChecked('true')).toBe(true);
    });
  });

  /**
   * 保存的默认条件（对标卖家精灵「保存当前模式」）。
   *
   * 这组守的是**三级优先级**：会话快照 > 保存的条件 > 内置默认。三者搞混的后果各不
   * 相同且都难归因——保存项穿透快照，用户离开前调的条件被几天前保存的顶掉；保存项没
   * 盖住内置默认，用户保存了却没生效、还以为自己没点到按钮。
   */
  describe('保存的默认条件', () => {
    const KEY = (userId: number) => `keywordiq.analysis.savedMode.v1.${userId}`;

    /** 直接往 localStorage 塞一份保存项，模拟「上次登录存过」。 */
    const seedSaved = (userId: number, over?: Record<string, unknown>) =>
      window.localStorage.setItem(
        KEY(userId),
        JSON.stringify({
          version: 1,
          site: 'DE',
          view: 'DAY',
          filters: {
            categories: [],
            ranking: {},
            change: {},
            clickShare: {},
            conversionShare: {},
            isNew: undefined,
            brands: [],
            productTitle: '',
          },
          sort: { field: 'current_ranking', dir: 'asc' },
          ...over,
        }),
      );

    const saveButton = () => screen.getByRole('button', { name: /保存当前模式/ });

    it('没保存过时按内置默认初始化，不显示「我的默认」', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      expect(searchAnalysis.mock.calls[0][0]).toMatchObject({ site: 'US', is_new: true });
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
    });

    it('保存过时按保存的作用域解析并显示「我的默认」', async () => {
      seedSaved(1, { site: 'DE' });

      await renderSettled(false);
      await waitFor(() => expect(getScope).toHaveBeenCalled());

      // 首屏问的是保存的站点，不是默认站点
      expect(getScope.mock.calls[0][0]).toBe('DE');
      expect(await screen.findByText('我的默认')).toBeInTheDocument();
    });

    it('保存的筛选盖过内置默认（存的是「全部」就不该变回「只看新词」）', async () => {
      seedSaved(1);

      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      expect(searchAnalysis.mock.calls[0][0].is_new).toBeUndefined();
    });

    /**
     * 保存项**不得穿透会话快照**。切走再切回是「接着刚才干」，不是「重新开始」——
     * 让保存项在这里生效，等于把用户离开前调的条件换成他几天前存的那组。
     */
    it('有会话快照时保存项不参与，且一个请求都不发', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      seedSaved(1, { site: 'DE' }); // 快照已在，此刻才出现的保存项不该被采用
      cleanup();
      vi.clearAllMocks();

      await renderSettled(false);
      await settle();

      expect(getScope).not.toHaveBeenCalled();
      expect(searchAnalysis).not.toHaveBeenCalled();
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
    });

    it('换个账号登录看不到别人保存的条件', async () => {
      seedSaved(1, { site: 'DE' });
      userState.currentUser = { id: 2 };

      await renderSettled(false);
      await waitFor(() => expect(getScope).toHaveBeenCalled());

      expect(getScope.mock.calls[0][0]).toBeUndefined(); // 默认站点，不是甲的 DE
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
    });

    it('版本不匹配就丢弃，按内置默认初始化', async () => {
      seedSaved(1, { site: 'DE', version: 99 });

      await renderSettled(false);
      await waitFor(() => expect(getScope).toHaveBeenCalled());

      expect(getScope.mock.calls[0][0]).toBeUndefined();
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
      expect(window.localStorage.getItem(KEY(1))).toBeNull(); // 坏数据顺手删掉
    });

    /**
     * 站点下线：后端对非法站点报错且不回退（既有约定），前端必须自己兜，并且**说出来**。
     * 静默回退比报错更糟——用户以为在看自己保存的站点，实际在看另一个。
     */
    it('保存的站点已下线时回退默认站点、清掉保存项并明示', async () => {
      seedSaved(1, { site: 'ZZ' });
      getScope.mockImplementation(async (site?: string) => {
        if (site === 'ZZ') throw new Error('站点不存在');
        return {
          site: 'US',
          view: 'DAY',
          latest_date: '2026-07-18',
          available_dates: ['2026-07-18', '2026-07-17'],
        };
      });

      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      expect(searchAnalysis.mock.calls[0][0]).toMatchObject({ site: 'US' });
      expect(window.localStorage.getItem(KEY(1))).toBeNull();
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
      expect(await screen.findByText(/保存的站点已下线/)).toBeInTheDocument();
    });

    it('点「保存当前模式」把生效条件写进 localStorage', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      await act(async () => {
        fireEvent.click(saveButton());
      });

      const stored = JSON.parse(window.localStorage.getItem(KEY(1))!);
      expect(stored).toMatchObject({ version: 1, site: 'US', view: 'DAY' });
      expect(stored.filters.isNew).toBe(true);
      // 检索词不入库：一次性查找不是「常看的模式」
      expect(stored.filters.q).toBeUndefined();
    });

    /**
     * 存的必须是用户**看到过结果**的那组。草稿改了没点「开始筛选」就存，下次登录进屏
     * 看到的东西他不认识。
     */
    it('有未生效改动时拒绝保存', async () => {
      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      // 改草稿但不点「开始筛选」
      fireEvent.change(screen.getByPlaceholderText(/输入 ASIN 或关键词/), {
        target: { value: 'coffee' },
      });
      await act(async () => {
        fireEvent.click(saveButton());
      });

      expect(window.localStorage.getItem(KEY(1))).toBeNull();
      expect(await screen.findByText(/请先点「开始筛选」/)).toBeInTheDocument();
    });

    it('还没查询成功时保存按钮禁用', async () => {
      getScope.mockResolvedValue({
        site: 'US',
        view: 'DAY',
        latest_date: null,
        available_dates: [],
      });

      await renderSettled();
      await settle();

      expect(saveButton()).toBeDisabled();
    });

    it('「重置条件」连带删除保存项', async () => {
      seedSaved(1);

      await renderSettled();
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      expect(screen.getByText('我的默认')).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /重置条件/ }));
      });

      expect(window.localStorage.getItem(KEY(1))).toBeNull();
      expect(screen.queryByText('我的默认')).not.toBeInTheDocument();
    });
  });

});
