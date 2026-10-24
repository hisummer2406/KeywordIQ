import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import {
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
import { renderSettled, submit } from './__testing__/analysisHarness';

const FAILURE_NOTICE = /查询失败，下表数据未更新/;

/**
 * 查询失败不得伪装成空数据或新数据（issue #92，断网 / 服务不可达 / 请求失败）。
 *
 * 失败的请求不写任何结果状态，而 `hasQueried` 在请求发出之前就已经变真——于是没有这层
 * 处理时，首次查询失败的界面是「搜索结果：0」+ 空表格，读起来是「条件没命中」，实际是
 * 「没查成」；后续查询失败则是表格留着上一组条件的行、「有未生效改动」也不亮。
 * 两种都没有任何报错，只有一条几秒就消失的 toast。
 *
 * 全局 errorHandler 在这里被 mock 掉的服务层绕过了（它属于 app.tsx，有自己的测试），
 * 所以这些用例只断言**页面自己**该呈现的部分。
 */
describe('分析查询屏 · 请求失败', () => {
  beforeEach(() => {
    seedAnalysisMocks();
    clearSnapshot();
    window.localStorage.clear();
  });

  it('再次查询时中止上一次在飞的请求，被中止的那次不算失败（#154）', async () => {
    await renderSettled(false);
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(1));

    // 第二次请求卡在网络上，直到被中止——按 axios 的形状以 CanceledError 拒绝
    searchAnalysis.mockImplementationOnce(
      (_body: unknown, options?: { signal?: AbortSignal }) =>
        new Promise((_, reject) => {
          options?.signal?.addEventListener('abort', () =>
            reject(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' })),
          );
        }),
    );
    await act(async () => {
      fireEvent.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(2));
    const pendingSignal: AbortSignal = searchAnalysis.mock.calls[1][1].signal;
    expect(pendingSignal.aborted).toBe(false);

    await act(async () => {
      fireEvent.click(submit());
    });
    await waitFor(() => expect(searchAnalysis).toHaveBeenCalledTimes(3));
    expect(pendingSignal.aborted).toBe(true);
    expect(searchAnalysis.mock.calls[2][1].signal.aborted).toBe(false);

    // 最新那次成功落地：结果照常显示，被中止的那次没有留下失败提示
    expect(await screen.findByText(/搜索结果：/)).toBeInTheDocument();
    expect(screen.queryByText(FAILURE_NOTICE)).not.toBeInTheDocument();
  });

  it('首次查询就失败：不显示「搜索结果：0」，改为常驻的失败提示', async () => {
    searchAnalysis.mockRejectedValue(new Error('Network Error'));

    await renderSettled(false);

    expect(await screen.findByText(FAILURE_NOTICE)).toBeInTheDocument();
    expect(screen.queryByText(/搜索结果：/)).not.toBeInTheDocument();
  });

  it('失败后再次查询成功：提示消失，条数恢复', async () => {
    searchAnalysis.mockRejectedValueOnce(new Error('Network Error'));

    await renderSettled(false);
    expect(await screen.findByText(FAILURE_NOTICE)).toBeInTheDocument();

    // 网络恢复后，用户重新点「立即查询」即为重试
    await act(async () => {
      fireEvent.click(submit());
    });

    await waitFor(() => expect(screen.queryByText(FAILURE_NOTICE)).not.toBeInTheDocument());
    expect(await screen.findByText(/搜索结果：/)).toBeInTheDocument();
  });

  it('已有结果后再次查询失败：不再显示旧条数，也不把旧行当成新条件的结果', async () => {
    await renderSettled(false);
    expect(await screen.findByText(/搜索结果：/)).toBeInTheDocument();

    searchAnalysis.mockRejectedValueOnce(new Error('Network Error'));
    await act(async () => {
      fireEvent.click(submit());
    });

    expect(await screen.findByText(FAILURE_NOTICE)).toBeInTheDocument();
    expect(screen.queryByText(/搜索结果：/)).not.toBeInTheDocument();
  });

  it('服务端错误同样呈现，不只是断网', async () => {
    // 服务端 5xx 与断网在页面这一层长得一样：请求都没有产出结果。区分提示文案是 errorHandler 的事
    searchAnalysis.mockRejectedValue(Object.assign(new Error('boom'), { response: { status: 503 } }));

    await renderSettled(false);

    expect(await screen.findByText(FAILURE_NOTICE)).toBeInTheDocument();
  });

  it('响应处理段抛错（请求本身成功）同样呈现失败并留下日志，不静默吞掉', async () => {
    // ProTable 在 onRequestError 存在时吞掉 request 里的一切异常。这里让响应形状不对
    // （`res.total` 读 undefined 抛 TypeError），模拟处理段的代码缺陷：没有 toast，
    // 若 try 只包住 searchAnalysis，界面会停在「搜索结果：0」且控制台零输出。
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    searchAnalysis.mockResolvedValue(undefined);

    await renderSettled(false);

    expect(await screen.findByText(FAILURE_NOTICE)).toBeInTheDocument();
    expect(screen.queryByText(/搜索结果：/)).not.toBeInTheDocument();
    expect(logged).toHaveBeenCalledWith('[Analysis] 查询请求异常', expect.any(TypeError));
    logged.mockRestore();
  });
});
