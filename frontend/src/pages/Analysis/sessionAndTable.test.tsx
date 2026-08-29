import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

import AnalysisPage from '@/pages/Analysis';
import { clearSnapshot, readSnapshot, writeSlot } from '@/pages/Analysis/stateCache';
import { renderSettled, settle, submit } from './__testing__/analysisHarness';

/**
 * 会话快照的复原语义、加入词库入口，以及结果表格的列宽必须真正生效。
 *
 * 分析屏的用例按主题分成四个文件（本文件是其中之一），**不是按体量硬切**：这一屏的
 * 用例曾集中在一个文件里跑 42.7 秒，而 vitest 按文件并行，一个文件就成了整个套件的
 * 关键路径（当时套件总耗时约 49 秒）。拆开之后各文件可以并行跑。
 *
 * 共用的替身与默认数据在 `__testing__/analysisMocks`，渲染帮手在
 * `__testing__/analysisHarness`。**每个文件都要自己调 `seedAnalysisMocks()`
 * 与 `clearSnapshot()`**——模块级快照与 localStorage 都跨用例存活。
 */
describe('分析查询屏 · 切走再切回与结果表格', () => {
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

  /**
   * 切走再切回。umi 没有 keepalive，切换路由会真的卸载这个组件，所以「重新挂载一次」
   * 就是对切回来的忠实模拟——不清模块级缓存即可。
   */
  describe('切走再切回', () => {
    it('复原时一个请求都不发', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      cleanup();
      vi.clearAllMocks();

      await renderSettled(false); // 第二次挂载 = 切回来，不点查询
      await settle();

      expect(getSites).not.toHaveBeenCalled();
      expect(getScope).not.toHaveBeenCalled();
      expect(getCategories).not.toHaveBeenCalled();
      expect(searchAnalysis).not.toHaveBeenCalled();
    });

    it('复原后仍显示离开前的结果行', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      cleanup();
      vi.clearAllMocks();

      await renderSettled(false);
      expect(await screen.findByText('coffee maker')).toBeInTheDocument();
    });

    it('复原后点「立即查询」仍然真的去查（强制刷新的唯一入口不能被短路吃掉）', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      cleanup();
      vi.clearAllMocks();

      await renderSettled(false);
      await settle();
      expect(searchAnalysis).not.toHaveBeenCalled(); // 复原那次确实没查

      await act(async () => {
        fireEvent.click(submit());
      });
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));
    });

    /**
     * 滚动恢复只验到「接线正确」这一层：复原且数据就绪后，确实拿离开前那个值调了
     * `scrollTo`。真实浏览器里的时机（行渲染完没有、页面够不够高）jsdom 表达不了，
     * 那部分要手测。
     */
    it('复原后按离开前的滚动量调用 scrollTo', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      // 模拟用户滚动：jsdom 不会自己派发 scroll，直接写快照即可——被测的是恢复那一侧
      writeSlot({ scrollY: 900 });
      cleanup();
      vi.clearAllMocks();

      const scrollTo = vi.fn();
      vi.stubGlobal('scrollTo', scrollTo);
      // 页面「够高」，否则恢复逻辑会逐帧等下去
      vi.spyOn(document.documentElement, 'scrollHeight', 'get').mockReturnValue(5000);

      await renderSettled(false);
      await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 900 }));
      vi.unstubAllGlobals();
    });

    it('首次进入（没有缓存）不动滚动位置', async () => {
      const scrollTo = vi.fn();
      vi.stubGlobal('scrollTo', scrollTo);

      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      await settle();

      expect(scrollTo).not.toHaveBeenCalled();
      vi.unstubAllGlobals();
    });

    it('刷新页面（缓存清空）后回到完整初始化', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      cleanup();
      vi.clearAllMocks();
      clearSnapshot(); // 刷新页面 = 模块级缓存随页面一起没了

      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      expect(getSites).toHaveBeenCalled();
      expect(getScope).toHaveBeenCalled();
    });

    /**
     * 卸载之后迟到的响应不得写快照。
     *
     * 这是本文件里唯一一条不为界面行为、而为**测试可信度**存在的用例。快照是模块级的、
     * 跨用例存活：上一个用例切走时还在飞的请求若在下一个用例的 `clearSnapshot()` 之后
     * 落地，就会把整份草稿写回来（实测一次 `getSites` 迟到即写回 7 个键）。下一个用例
     * 挂载时读到非空快照，于是走复原短路、一个请求都不发——一批与改动毫无关系的用例
     * 随机翻红，且每次翻红的都不是同一批，单独跑那个文件又永远是绿的。
     *
     * 生产上同一个机制的表现是：用户切走时那次查询的结果，在他切回来时冒出来。
     */
    it('切走之后迟到的响应不写快照（否则下一次挂载会凭空走复原短路）', async () => {
      let release: (v: unknown) => void = () => {};
      getSites.mockReturnValue(
        new Promise((r) => {
          release = r;
        }),
      );

      render(<AnalysisPage />);
      cleanup(); // 用户切走 / 上一个用例结束
      clearSnapshot();

      await act(async () => {
        release([{ code: 'US', name: '美国', domain: 'amazon.com' }]);
        await Promise.resolve();
      });

      expect(readSnapshot()).toBeNull();
    });
  });

  /**
   * 加入词库：分析屏是**唯一**的入库入口，所以这几条守的是「入口在、且指向对的分组」。
   */
  describe('加入词库', () => {
    /** 行复选框。第 0 个是表头的全选，行的从 1 开始。 */
    const rowCheckbox = () => screen.getAllByRole('checkbox')[1];
    const addButton = () => screen.queryByRole('button', { name: /加入词库/ });

    it('没勾行时不显示入口', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      expect(addButton()).toBeNull();
    });

    it('勾选后出现入口并带上条数', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      fireEvent.click(rowCheckbox());

      await waitFor(() => expect(addButton()).toBeTruthy());
      expect(addButton()!.textContent).toContain('1');
    });

    it('弹窗只列当前生效站点的分组', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      fireEvent.click(rowCheckbox());
      await waitFor(() => expect(addButton()).toBeTruthy());

      await act(async () => {
        fireEvent.click(addButton()!);
      });

      await waitFor(() => expect(listGroups).toHaveBeenCalledWith('US'));
    });

    it('加入成功后清空选中，入口随之消失', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      fireEvent.click(rowCheckbox());
      await waitFor(() => expect(addButton()).toBeTruthy());
      await act(async () => {
        fireEvent.click(addButton()!);
      });
      await waitFor(() => expect(listGroups).toHaveBeenCalled());

      // antd 会在两个汉字之间插一个空格（「加 入」），所以名字要留出空白位；
      // 又因为工具栏那个「加入词库」也匹配，必须限定在弹窗内找。
      await act(async () => {
        const dialog = screen.getByRole('dialog');
        fireEvent.click(within(dialog).getByRole('button', { name: /^加\s*入$/ }));
      });

      await waitFor(() => expect(addItems).toHaveBeenCalledWith(7, [1]));
      await waitFor(() => expect(addButton()).toBeNull());
    });

    it('切走再切回，选中状态清空（它不进会话快照）', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      fireEvent.click(rowCheckbox());
      await waitFor(() => expect(addButton()).toBeTruthy());

      cleanup();
      await renderSettled(false);
      await settle();

      expect(addButton()).toBeNull();
    });

    it('没有工具箱权限就没有这个入口', async () => {
      accessState.canViewKeyword = false;
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());
      fireEvent.click(rowCheckbox());
      await settle();

      expect(addButton()).toBeNull();
    });
  });

  /**
   * 列宽能不能生效，取决于表格用的是 `table-layout: fixed` 还是 `auto`。
   *
   * 这条守的是**升级导致的无声退化**：rc-table 早期版本里「给了 `scroll.x` 就切 fixed」
   * 成立，7.54 之后不再成立（判据只剩：显式 tableLayout / 固定列 / scroll.y 或粘性头 /
   * 某列开 ellipsis）。退化时没有任何报错，只是列宽悄悄变回建议值——实测关键词列 220
   * 渲染成 247、表格 1578 撑到 1792。见 #43。
   *
   * 断言落在**渲染出来的 table 元素**上而不是 props：props 只能证明我们传了什么，
   * 证明不了 rc-table 最终采信了什么。
   */
  describe('结果表格的列宽必须真正生效', () => {
    it('表格用 fixed 布局，不是 auto', async () => {
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      const table = document.querySelector('.ant-table table') as HTMLTableElement;
      expect(table).toBeTruthy();
      expect(table.style.tableLayout).toBe('fixed');
    });

    it('每一列都带宽度，包括复选框列', async () => {
      // 少一列没宽度，fixed 布局下它就吃掉全部富余，其余列反而被压
      await renderSettled(false);
      await waitFor(() => expect(searchAnalysis).toHaveBeenCalled());

      const cols = Array.from(document.querySelectorAll('.ant-table table colgroup col'));
      expect(cols.length).toBeGreaterThan(1);
      expect(cols.every((col) => (col as HTMLElement).style.width !== '')).toBe(true);
    });
  });
});
