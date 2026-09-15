import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getTopslotHistory } = vi.hoisted(() => ({ getTopslotHistory: vi.fn() }));
vi.mock('@/services/topslotHistory', () => ({ getTopslotHistory }));

import TopslotHistoryPopover from './TopslotHistoryPopover';

/**
 * 悬浮历史面板的契约测试（issue #96）：悬浮才发请求、分页追加、竞态防护、
 * 五种状态（首次加载/追加加载/空记录/失败可重试/全部加载完成）。
 *
 * 不测 antd Popover 自身的定位/动画——那是 antd 的职责；这里只关心"悬浮触发后
 * 面板内容按状态正确呈现"，用 `waitFor` 越过 `mouseEnterDelay` 与异步请求的结算。
 */

function item(date: string, rank: number) {
  return { date, rank };
}

function baseProps(overrides: Partial<Parameters<typeof TopslotHistoryPopover>[0]> = {}) {
  return {
    site: 'US',
    view: 'DAY' as const,
    keywordId: 1,
    on: '2026-07-10',
    kind: 'ASIN' as const,
    value: 'B0EXAMPLE1',
    label: 'B0EXAMPLE1',
    children: <span data-testid="trigger">trigger</span>,
    ...overrides,
  };
}

beforeEach(() => {
  getTopslotHistory.mockReset();
});

function hover() {
  fireEvent.mouseEnter(screen.getByTestId('trigger'));
}

describe('悬浮才发请求', () => {
  it('渲染时不发起任何请求', () => {
    render(<TopslotHistoryPopover {...baseProps()} />);
    expect(getTopslotHistory).not.toHaveBeenCalled();
  });

  it('悬浮后发起首次请求，参数正确', async () => {
    getTopslotHistory.mockResolvedValue({ success: true, total: 0, items: [] });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(1));
    expect(getTopslotHistory).toHaveBeenCalledWith(
      expect.objectContaining({
        site: 'US', view: 'DAY', keywordId: 1, kind: 'ASIN', value: 'B0EXAMPLE1',
        on: '2026-07-10', page: 1,
      }),
    );
  });
});

describe('五种状态', () => {
  it('首次加载中显示加载态（Spin），不是空面板也不是错误', async () => {
    getTopslotHistory.mockReturnValue(new Promise(() => {})); // 永不结算
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(document.querySelector('.ant-spin')).toBeTruthy());
    expect(screen.queryByText('加载失败')).toBeNull();
    expect(screen.queryByText('没有匹配历史')).toBeNull();
  });

  it('空记录显示空状态而不是「0 次」当成错误', async () => {
    getTopslotHistory.mockResolvedValue({ success: true, total: 0, items: [] });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(screen.getByText('没有匹配历史')).toBeTruthy());
  });

  it('加载失败显示失败态且不显示为 0 次，提供重试', async () => {
    getTopslotHistory.mockRejectedValueOnce(new Error('network'));
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(screen.getByText('加载失败')).toBeTruthy());
    expect(screen.queryByText(/上榜次数（0）/)).toBeNull();

    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 1, items: [item('2026-07-10', 1)],
    });
    // antd Button 对两个汉字的文案会自动插入一个视觉空格（"重试" 渲染成"重 试"），
    // 用正则容忍这个中间空格，不依赖具体渲染细节。
    fireEvent.click(screen.getByText(/重\s*试/));

    await waitFor(() => expect(screen.getByText(/上榜次数（1）/)).toBeTruthy());
  });

  it('加载完成后展示总次数与逐条记录', async () => {
    getTopslotHistory.mockResolvedValue({
      success: true,
      total: 2,
      items: [item('2026-07-10', 1), item('2026-07-08', 3)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(screen.getByText(/上榜次数（2）/)).toBeTruthy());
    expect(screen.getByText('第 1 名')).toBeTruthy();
    expect(screen.getByText('第 3 名')).toBeTruthy();
  });

  it('已加载全部记录后提示"已展示全部记录"', async () => {
    getTopslotHistory.mockResolvedValue({
      success: true, total: 1, items: [item('2026-07-10', 1)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();

    await waitFor(() => expect(screen.getByText('已展示全部记录')).toBeTruthy());
  });
});

describe('触底追加分页', () => {
  it('滚动触底后请求下一页并追加，不覆盖已显示的总次数', async () => {
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 3, items: [item('2026-07-10', 1), item('2026-07-09', 2)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();
    await waitFor(() => expect(screen.getByText(/上榜次数（3）/)).toBeTruthy());

    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 999, items: [item('2026-07-08', 3)], // 后续页 total 不应生效
    });
    const scroller = screen.getByTestId('topslot-history-scroll');
    Object.defineProperty(scroller, 'scrollHeight', { value: 300, configurable: true });
    Object.defineProperty(scroller, 'clientHeight', { value: 260, configurable: true });
    Object.defineProperty(scroller, 'scrollTop', { value: 40, configurable: true });
    fireEvent.scroll(scroller);

    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(2));
    expect(getTopslotHistory).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    await waitFor(() => expect(screen.getByText('已展示全部记录')).toBeTruthy());
    // 总次数保持首次加载的 3，不被第二页返回的 999 覆盖
    expect(screen.getByText(/上榜次数（3）/)).toBeTruthy();
  });

  it('未触底不追加请求', async () => {
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 5, items: [item('2026-07-10', 1)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();
    await waitFor(() => expect(screen.getByText(/上榜次数（5）/)).toBeTruthy());

    const scroller = screen.getByTestId('topslot-history-scroll');
    Object.defineProperty(scroller, 'scrollHeight', { value: 900, configurable: true });
    Object.defineProperty(scroller, 'clientHeight', { value: 260, configurable: true });
    Object.defineProperty(scroller, 'scrollTop', { value: 10, configurable: true });
    fireEvent.scroll(scroller);

    expect(getTopslotHistory).toHaveBeenCalledTimes(1);
  });
});

describe('竞态防护与请求去重', () => {
  // 「快速切换查看对象」这条验收条件（issue #96）由组件划分方式在结构上保证：每个
  // ASIN/品牌各自独立一个组件实例（见文件头注释），不是共享一个实例切换 value——
  // 悬浮到 A 与悬浮到 B 是两个不同实例各自的请求，互不可见，不存在"B 的响应覆盖 A"
  // 这类跨对象串位。真实浏览器验证时仍应人工快速切换鼠标悬浮的行/槽位核实一次
  // （见 tasks.md 收尾验证一节），这里改为验证同一实例内可测的那部分：请求去重。
  it('上一页仍在加载时再次触底不重复发起请求', async () => {
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 3, items: [item('2026-07-10', 1), item('2026-07-09', 2)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();
    await waitFor(() => expect(screen.getByText(/上榜次数（3）/)).toBeTruthy());

    let resolveSecond: (value: unknown) => void = () => {};
    getTopslotHistory.mockImplementationOnce(
      () => new Promise((resolve) => { resolveSecond = resolve; }),
    );
    const scroller = screen.getByTestId('topslot-history-scroll');
    Object.defineProperty(scroller, 'scrollHeight', { value: 300, configurable: true });
    Object.defineProperty(scroller, 'clientHeight', { value: 260, configurable: true });
    Object.defineProperty(scroller, 'scrollTop', { value: 40, configurable: true });

    fireEvent.scroll(scroller); // 第一次触底：发起第 2 页请求，尚未结算
    fireEvent.scroll(scroller); // 第二次触底：此时仍在加载中，不该再发一次
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(2));

    resolveSecond({ success: true, total: 3, items: [item('2026-07-08', 3)] });
    await waitFor(() => expect(screen.getByText('已展示全部记录')).toBeTruthy());
    // 全程只有"首次加载 + 一次追加"两次请求，第二次触底被 loadingMore 挡住
    expect(getTopslotHistory).toHaveBeenCalledTimes(2);
  });
});
