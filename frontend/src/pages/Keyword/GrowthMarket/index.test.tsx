import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const { searchGrowth, getGrowthScope, getSites } = vi.hoisted(() => ({
  searchGrowth: vi.fn(),
  getGrowthScope: vi.fn(),
  getSites: vi.fn(),
}));

vi.mock('@umijs/max', () => ({ useAccess: () => ({}) }));
vi.mock('@/services/keyword', () => ({ searchGrowth, getGrowthScope }));
vi.mock('@/services/upload', () => ({ getSites }));

import GrowthMarketPage from './index';

const submit = () => screen.getByRole('button', { name: /立即查询/ });

/** 只挂载。**首屏不自动查榜单**，所以渲染完表格是空的。 */
async function renderSettled() {
  await act(async () => {
    render(<GrowthMarketPage />);
  });
}

/** 点一次「立即查询」，等按钮可用之后再点（作用域取回前它是禁用的）。 */
async function clickSubmit() {
  await waitFor(() => expect(submit()).not.toBeDisabled());
  await act(async () => {
    fireEvent.click(submit());
  });
}

/**
 * 挂载 + 点一次「立即查询」。下面多数用例断言的是「已经查过一次」之后的行为，而这一屏
 * 的第一次查询必须由用户动作触发——helper 替它点，不是绕过那条规则，是把规则演一遍。
 */
async function renderQueried() {
  await renderSettled();
  await clickSubmit();
}

/** 给「之后没有再发请求」这类断言一个观察窗口。 */
async function settle(ms = 60) {
  await act(async () => {
    await new Promise((resolve) => {
      setTimeout(resolve, ms);
    });
  });
}

const SCOPE = {
  site: 'US',
  latest_date: '2026-08-15',
  available_dates: ['2026-08-15', '2026-08-14'],
  window_days: 7,
  min_rank_gain: 10000,
  min_daily_rate: 0.1,
};

const ROW = {
  keyword_id: 1,
  keyword: 'air fryer',
  start_ranking: 60000,
  end_ranking: 18000,
  rank_gain: 42000,
  min_growth_rate: 0.12,
  trend: [60000, 51000, 43000, 36000, 30000, 24000, 18000],
  trend_dates: [
    '2026-08-09', '2026-08-10', '2026-08-11', '2026-08-12',
    '2026-08-13', '2026-08-14', '2026-08-15',
  ],
};

/**
 * 这一屏守四件事：**首屏不自动查**（与分析屏相反，那一屏进去就查）、口径文案取自服务端、
 * 缺期必须说出来、以及「查询显式触发」的纪律。
 */
describe('持续增长市场', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSites.mockResolvedValue([
      { code: 'US', name_zh: '美国', domain: 'amazon.com' },
      { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
    ]);
    getGrowthScope.mockResolvedValue(SCOPE);
    searchGrowth.mockResolvedValue({
      success: true, data: [ROW], total: 1,
      site: 'US', date: '2026-08-15', missing_dates: [], window_days: 7,
    });
  });

  /**
   * 首屏不自动查榜单——榜单按锚定日切片，默认那一天（最新一期）恰恰最可能因窗口缺期或
   * 口径过严而为空，先查一次既浪费请求又让用户一进来就看到空表。
   *
   * 随之而来的义务：空表格有三种成因（还没查 / 窗口缺期 / 口径下没命中），含义完全不同
   * 却长得一样，所以「还没查」这一种必须在三处同时说清。
   */
  describe('首屏', () => {
    it('只拉作用域，不查榜单', async () => {
      await renderSettled();

      await waitFor(() => expect(getGrowthScope).toHaveBeenCalled());
      expect(getSites).toHaveBeenCalled();
      await settle();
      expect(searchGrowth).not.toHaveBeenCalled();
    });

    it('点立即查询后才取数，条件是所选站点与锚定日', async () => {
      await renderSettled();
      await waitFor(() => expect(getGrowthScope).toHaveBeenCalled());
      expect(searchGrowth).not.toHaveBeenCalled();

      await clickSubmit();

      await waitFor(() => expect(searchGrowth).toHaveBeenCalledTimes(1));
      expect(searchGrowth.mock.calls[0][0]).toMatchObject({ site: 'US', date: '2026-08-15' });
      expect(await screen.findByText('air fryer')).toBeTruthy();
    });

    it('未查询时不显示条数（「持续增长词：0」会被读成今天没有增长词）', async () => {
      await renderSettled();
      await settle();

      expect(screen.queryByText(/持续增长词：/)).toBeNull();
    });

    it('未查询时空表格明说要点查询，而不是「暂无数据」', async () => {
      await renderSettled();
      await settle();

      expect(screen.getByText(/点「立即查询」查看榜单/)).toBeTruthy();
    });

    it('未查询时不提示「条件已修改」（一个条件都没改，那句话是错的）', async () => {
      await renderSettled();
      await settle();

      expect(screen.queryByText(/条件已修改/)).toBeNull();
    });

    it('查询过之后条数才出现', async () => {
      await renderQueried();

      expect(await screen.findByText(/持续增长词：/)).toBeTruthy();
    });
  });

  it('口径文案里的数值来自服务端回传，不是写死的', async () => {
    getGrowthScope.mockResolvedValue({ ...SCOPE, window_days: 14, min_rank_gain: 5000, min_daily_rate: 0.05 });

    await renderSettled();

    expect(await screen.findByText(/近 14 天/)).toBeTruthy();
    expect(screen.getByText(/5,000 名/)).toBeTruthy();
  });

  it('缺期时给出提示，说明榜单空是因为缺数据', async () => {
    searchGrowth.mockResolvedValue({
      success: true, data: [], total: 0, site: 'US', date: '2026-08-15',
      missing_dates: ['2026-08-11', '2026-08-12'], window_days: 7,
    });

    await renderQueried();

    expect(await screen.findByText(/窗口内有期次缺少数据/)).toBeTruthy();
    expect(screen.getByText(/2026-08-11、2026-08-12/)).toBeTruthy();
  });

  it('窗口完整时不显示缺期提示', async () => {
    await renderQueried();
    await waitFor(() => expect(searchGrowth).toHaveBeenCalled());

    expect(screen.queryByText(/窗口内有期次缺少数据/)).toBeNull();
  });

  it('改日期不点查询就不发请求', async () => {
    await renderQueried();
    await waitFor(() => expect(searchGrowth).toHaveBeenCalledTimes(1));

    fireEvent.mouseDown(screen.getByTitle('2026-08-15'));
    await act(async () => {
      fireEvent.click(await screen.findByTitle('2026-08-14'));
    });
    await settle();

    expect(searchGrowth).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/条件已修改/)).toBeTruthy();
  });

  it('点立即查询后新条件才生效，提示消失', async () => {
    await renderQueried();
    await waitFor(() => expect(searchGrowth).toHaveBeenCalledTimes(1));

    fireEvent.mouseDown(screen.getByTitle('2026-08-15'));
    await act(async () => {
      fireEvent.click(await screen.findByTitle('2026-08-14'));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /立即查询/ }));
    });

    await waitFor(() => expect(searchGrowth).toHaveBeenCalledTimes(2));
    expect(searchGrowth.mock.calls[1][0]).toMatchObject({ date: '2026-08-14' });
    await waitFor(() => expect(screen.queryByText(/条件已修改/)).toBeNull());
  });

  it('切站点重拉日期清单，但不重查表格', async () => {
    await renderQueried();
    await waitFor(() => expect(searchGrowth).toHaveBeenCalledTimes(1));
    getGrowthScope.mockResolvedValue({ ...SCOPE, site: 'DE', available_dates: ['2026-08-13'], latest_date: '2026-08-13' });

    fireEvent.mouseDown(screen.getByTitle('美国站（US）'));
    await act(async () => {
      fireEvent.click(await screen.findByTitle('德国站（DE）'));
    });
    await settle();

    expect(getGrowthScope).toHaveBeenLastCalledWith('DE');
    // 不重查：否则用户会在一份属于上一个站点的日期清单里做选择
    expect(searchGrowth).toHaveBeenCalledTimes(1);
  });
});
