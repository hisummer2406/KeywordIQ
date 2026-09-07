import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getKeywordTrend } = vi.hoisted(() => ({ getKeywordTrend: vi.fn() }));
vi.mock('@/services/keywordTrend', () => ({ getKeywordTrend }));

// 抽屉内嵌了 AddToLibraryModal，其 `@/services/library` 真实实现会 `import { request }
// from '@umijs/max'`——在 vitest 环境下加载真实的 `@umijs/max` 会触发一个与本文件无关的
// esbuild 环境不变量报错（"new TextEncoder().encode('') instanceof Uint8Array" 为
// false），是 Umi 运行时与 vitest 的 esbuild 转换服务共享/冲突导致的已知坑，不是这里的
// 逻辑问题。与 `AddToLibraryModal.test.tsx` 同样的做法：mock 掉 `@/services/library`，
// 这个文件从不测加入词库弹窗自身的行为（那边已经有独立测试），不需要真实实现。
vi.mock('@/services/library', () => ({
  listGroups: vi.fn().mockResolvedValue([]),
  createGroup: vi.fn(),
  addItems: vi.fn(),
}));

import KeywordTrendDrawer from './index';

/**
 * 抽屉的契约测试：不测 SVG 绘制细节（那是 TrendTimelineChart 自己的测试），
 * 测的是数据装配、竞态防护、加载失败与「加入词库」入口的显隐。
 */

function baseResponse(overrides: Partial<Awaited<ReturnType<typeof getKeywordTrend>>> = {}) {
  return {
    success: true,
    keyword_id: 1,
    keyword: 'coffee maker',
    site: 'US',
    view: 'DAY',
    range: 'RECENT_30D',
    on: '2026-07-10',
    coverage: { start: '2026-07-05', end: '2026-07-10' },
    points: [{ date: '2026-07-10', status: 'RANKED', ranking: 100 }],
    best_ranking: { date: '2026-07-10', ranking: 100 },
    latest_available: { date: '2026-07-10', ranking: 100 },
    ...overrides,
  };
}

beforeEach(() => {
  getKeywordTrend.mockReset();
});

function setup(props: Partial<Parameters<typeof KeywordTrendDrawer>[0]> = {}) {
  const onClose = vi.fn();
  render(
    <KeywordTrendDrawer
      open
      onClose={onClose}
      keywordId={1}
      keyword="coffee maker"
      site="US"
      view="DAY"
      on="2026-07-10"
      {...props}
    />,
  );
  return { onClose };
}

describe('KeywordTrendDrawer', () => {
  it('打开时按来源锚点请求数据，并展示统计条', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup();

    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledWith(
      expect.objectContaining({ keywordId: 1, site: 'US', view: 'DAY', range: 'RECENT_30D', on: '2026-07-10' }),
    ));
    expect(await screen.findByText('2026年07月10日')).toBeInTheDocument();
  });

  it('无有效点时区间最好排名与最近可用排名显示无数据', async () => {
    getKeywordTrend.mockResolvedValue(
      baseResponse({ points: [{ date: '2026-07-10', status: 'NOT_IMPORTED', ranking: null }], best_ranking: null, latest_available: null, coverage: null }),
    );
    setup();

    await screen.findByText('区间最好排名：');
    expect(screen.getAllByText('无数据').length).toBeGreaterThanOrEqual(2);
  });

  it('加载失败显示错误提示，不能显示成无数据', async () => {
    getKeywordTrend.mockRejectedValue(new Error('network'));
    setup();

    expect(await screen.findByText('加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByText('无数据')).not.toBeInTheDocument();
  });

  it('切换视图后仍使用同一个来源锚点重新请求', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup();
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText('按周'));

    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(2));
    expect(getKeywordTrend).toHaveBeenLastCalledWith(
      expect.objectContaining({ view: 'WEEK', on: '2026-07-10' }),
    );
  });

  it('旧请求的结果不覆盖新请求——只应用最后一次发出的那次', async () => {
    let resolveFirst: (v: unknown) => void = () => undefined;
    const first = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    getKeywordTrend.mockImplementationOnce(() => first);
    getKeywordTrend.mockResolvedValueOnce(baseResponse({ range: 'ALL', coverage: { start: '2026-01-01', end: '2026-07-10' } }));

    setup();
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText('全部可用历史'));
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(2));

    // 第二次请求先落地
    await screen.findByText('2026年07月10日');

    // 第一次（更早发出、更晚落地）的结果到达时不应覆盖已经展示的第二次结果
    resolveFirst(baseResponse({ on: '2026-06-01' }));
    await new Promise((r) => {
      setTimeout(r, 0);
    });
    expect(screen.queryByText('2026年06月01日')).not.toBeInTheDocument();
  });

  it('hideAddToLibrary 时不展示加入词库入口', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup({ hideAddToLibrary: true });

    await screen.findByText('2026年07月10日');
    expect(screen.queryByText('加入词库')).not.toBeInTheDocument();
  });

  it('未设置 hideAddToLibrary 时展示加入词库入口', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup();

    await screen.findByText('2026年07月10日');
    expect(screen.getByText('加入词库')).toBeInTheDocument();
  });
});
