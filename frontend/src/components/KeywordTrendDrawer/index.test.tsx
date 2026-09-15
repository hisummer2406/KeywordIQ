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
    range: 'ALL',
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
  it('打开时按来源锚点请求数据，默认范围为「全部可用历史」（issue #94）', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup();

    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledWith(
      expect.objectContaining({ keywordId: 1, site: 'US', view: 'DAY', range: 'ALL', on: '2026-07-10' }),
    ));
    expect(await screen.findByText('2026年07月10日')).toBeInTheDocument();
  });

  it('切换到另一个关键词时仍固定请求「全部可用历史」——范围不提供切换，恒为 ALL（issue #94）', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    const { rerender } = render(
      <KeywordTrendDrawer open onClose={vi.fn()} keywordId={1} keyword="coffee maker" site="US" view="DAY" on="2026-07-10" />,
    );
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(1));

    rerender(
      <KeywordTrendDrawer open onClose={vi.fn()} keywordId={2} keyword="another word" site="US" view="DAY" on="2026-07-10" />,
    );
    await waitFor(() => expect(getKeywordTrend).toHaveBeenLastCalledWith(
      expect.objectContaining({ keywordId: 2, range: 'ALL' }),
    ));
  });

  it('不再提供「近 30 天」切换入口（issue #94：用户反馈日常只用全部历史，去掉多余的一步选择）', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup();

    await screen.findByText('2026年07月10日');
    expect(screen.queryByText('近 30 天')).not.toBeInTheDocument();
    expect(screen.queryByText('全部可用历史')).not.toBeInTheDocument();
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
    getKeywordTrend.mockResolvedValueOnce(baseResponse({ view: 'WEEK' }));

    setup();
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText('按周'));
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(2));

    // 第二次请求先落地——响应的 view 是 WEEK，锚定期/覆盖范围/历史排名日期都按周格式
    // 渲染（"2026年第N周（...）"），不是 DAY 格式的"2026年07月10日"。用
    // findAllByText 而非 findByText：这条正则同时命中锚定期、覆盖范围、两个
    // StatLine 的日期几处文案，不是唯一匹配。
    expect((await screen.findAllByText(/2026年第\d+周/)).length).toBeGreaterThan(0);

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

  it('canAddToLibrary 为 false 时不展示加入词库入口（无 keyword:view 权限，PR #87 审核 P2）', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse());
    setup({ canAddToLibrary: false });

    await screen.findByText('2026年07月10日');
    expect(screen.queryByText('加入词库')).not.toBeInTheDocument();
  });

  it('切视图后新响应落地前，留存的旧数据仍按它自己的视图展示，不被新视图重新格式化（PR #87 复审第二轮 P2）', async () => {
    getKeywordTrend.mockResolvedValueOnce(baseResponse());
    setup();
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(1));
    await screen.findByText('2026年07月10日'); // DAY 格式：来自初次响应

    // 卡住切到按周触发的第二次请求（永不 resolve），制造"viewState 已经是 WEEK、
    // data 还是旧的 DAY 响应"这段窗口——这条用例只关心这段窗口内的展示是否正确，
    // 不需要观察请求最终落地后的状态（那属于既有用例"切换视图后仍使用同一个来源
    // 锚点重新请求"覆盖的范围）。
    getKeywordTrend.mockImplementationOnce(() => new Promise(() => {}));

    fireEvent.click(screen.getByText('按周'));
    await waitFor(() => expect(getKeywordTrend).toHaveBeenCalledTimes(2));

    // 这段窗口内界面留存的仍是第一次响应（DAY/2026-07-10），必须仍按 DAY 格式展示
    // ——若误按新选中的 WEEK 重新格式化，同一个日期会显示成"2026年第 N 周"，把 DAY
    // 历史读成了 WEEK 历史。
    expect(screen.getByText('2026年07月10日')).toBeInTheDocument();
    expect(screen.queryByText(/2026年第\d+周/)).not.toBeInTheDocument();
  });

  it('图例只常驻三种常见状态，不展示「该期报表已导入但未收录」与「已超出保留范围」（用户反馈"没见过"）', async () => {
    getKeywordTrend.mockResolvedValue(baseResponse({
      points: [
        { date: '2026-07-08', status: 'NO_DATA', ranking: null },
        { date: '2026-07-09', status: 'OUT_OF_RETENTION', ranking: null },
        { date: '2026-07-10', status: 'RANKED', ranking: 100 },
      ],
    }));
    setup();

    await screen.findByText('2026年07月10日');
    expect(screen.getByText('在榜')).toBeInTheDocument();
    expect(screen.getByText('已掉榜')).toBeInTheDocument();
    expect(screen.getByText('该期报表未导入')).toBeInTheDocument();
    // 少见状态即便真的出现在这次响应的数据里，图例也不常驻展示这两个标签
    expect(screen.queryByText('该期报表已导入但未收录')).not.toBeInTheDocument();
    expect(screen.queryByText('已超出保留范围')).not.toBeInTheDocument();
  });
});
