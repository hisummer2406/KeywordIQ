import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
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
  // 「快速切换查看对象」这条验收条件（issue #96）分两层：跨组件实例的部分由划分方式
  // 保证（每个 ASIN/品牌各自独立一个组件实例，见文件头注释），悬浮到 A 与悬浮到 B
  // 是两个不同实例各自的请求，互不可见，不存在"B 的响应覆盖 A"这类跨对象串位。
  // 但**同一个实例被 React 复用、身份 props 换了**这一层不能靠"结构不同"避开
  // （PR #97 Codex 审核 P2）——下面单独测。真实浏览器验证时仍应人工快速切换鼠标
  // 悬浮的行/槽位核实一次（见 tasks.md 收尾验证一节），这里先测同一实例内可测的
  // 那部分：请求去重。
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

  it('同一实例被复用、身份 props 变化时，旧身份的迟到响应不会渲染在新身份下面', async () => {
    // 模拟 ProTable 按 keyword_id 复用行组件：同一个 TopslotHistoryPopover 实例，
    // 悬浮时是 ASIN A，请求还没回来时来源数据已经换成了 ASIN B（比如切了查询日期）。
    let resolveFirst: (value: unknown) => void = () => {};
    getTopslotHistory.mockImplementationOnce(
      () => new Promise((resolve) => { resolveFirst = resolve; }),
    );
    const { rerender } = render(<TopslotHistoryPopover {...baseProps({ value: 'B0OLD' })} />);
    hover();
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(1));

    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 1, items: [item('2026-07-09', 2)],
    });
    // 身份变了：同一个实例（同一个 <Popover> 位置）换成新的 value/on
    rerender(<TopslotHistoryPopover {...baseProps({ value: 'B0NEW', on: '2026-07-09' })} />);
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(2));
    expect(getTopslotHistory).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'B0NEW', on: '2026-07-09' }),
    );
    await waitFor(() => expect(screen.getByText(/上榜次数（1）/)).toBeTruthy());

    // 旧身份（ASIN A）这时才姗姗来迟：不得覆盖已经展示的新身份（ASIN B）结果
    resolveFirst({ success: true, total: 999, items: [item('2020-01-01', 1)] });
    await Promise.resolve();
    expect(screen.getByText(/上榜次数（1）/)).toBeTruthy();
    expect(screen.queryByText(/上榜次数（999）/)).toBeNull();
  });

  it('面板还开着时身份 props 变化，直接按新身份重新加载（不需要用户重新悬浮）', async () => {
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 1, items: [item('2026-07-10', 1)],
    });
    const { rerender } = render(<TopslotHistoryPopover {...baseProps({ value: 'B0OLD' })} />);
    hover();
    await waitFor(() => expect(screen.getByText(/上榜次数（1）/)).toBeTruthy());

    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 2, items: [item('2026-07-09', 3), item('2026-07-08', 2)],
    });
    rerender(<TopslotHistoryPopover {...baseProps({ value: 'B0NEW' })} />);

    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(2));
    expect(getTopslotHistory).toHaveBeenLastCalledWith(
      expect.objectContaining({ value: 'B0NEW', page: 1 }),
    );
    await waitFor(() => expect(screen.getByText(/上榜次数（2）/)).toBeTruthy());
  });

  it('React 18 StrictMode 下（setup→cleanup→setup）响应仍能正常落地，不会卡在加载中', async () => {
    // 回归：mountedRef 曾经只在 cleanup 里置 false、setup 里不置 true，StrictMode
    // 的双调用会让它永远停在 false，此后每个响应都被 `!mountedRef.current` 挡住。
    getTopslotHistory.mockResolvedValue({
      success: true, total: 1, items: [item('2026-07-10', 1)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />, { wrapper: StrictMode });
    hover();

    await waitFor(() => expect(screen.getByText(/上榜次数（1）/)).toBeTruthy());
    expect(screen.queryByText('加载失败')).toBeNull();
  });

  it('关闭时有一次「加载更多」仍在飞，重新打开后不会卡在追加加载的转圈上', async () => {
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 5, items: [item('2026-07-10', 1), item('2026-07-09', 2)],
    });
    render(<TopslotHistoryPopover {...baseProps()} />);
    hover();
    await waitFor(() => expect(screen.getByText(/上榜次数（5）/)).toBeTruthy());

    // 触发一次"加载更多"，故意让它永不结算——模拟用户在它飞着的时候就移开了鼠标
    getTopslotHistory.mockImplementationOnce(() => new Promise(() => {}));
    const scroller = screen.getByTestId('topslot-history-scroll');
    Object.defineProperty(scroller, 'scrollHeight', { value: 300, configurable: true });
    Object.defineProperty(scroller, 'clientHeight', { value: 260, configurable: true });
    Object.defineProperty(scroller, 'scrollTop', { value: 40, configurable: true });
    fireEvent.scroll(scroller);
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(2));

    fireEvent.mouseLeave(screen.getByTestId('trigger'));
    // antd 的 hover 关闭走 mouseLeaveDelay（默认 0.1s）的真实定时器；不等它落定就
    // 立刻再 hover，rc-trigger 会把这当成"意图未离开"直接取消关闭，导致根本不会
    // 触发第二次 onOpenChange(true)，这个用例就测不到"重开"这个动作本身。
    await new Promise((resolve) => { setTimeout(resolve, 150); });

    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 5, items: [item('2026-07-10', 1), item('2026-07-09', 2)],
    });
    hover(); // 重新打开
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.getByText(/上榜次数（5）/)).toBeTruthy());

    // 重开经历了 loading → ready 两次不同的渲染分支，滚动容器是全新的 DOM 节点，
    // 必须重新查询——沿用重开前那个引用是已从树上摘掉的旧节点，滚动事件派发到它
    // 身上什么也触发不了，会把"loadingMore 没清空"这个真实 bug 误判成通过。
    const reopenedScroller = screen.getByTestId('topslot-history-scroll');
    Object.defineProperty(reopenedScroller, 'scrollHeight', { value: 300, configurable: true });
    Object.defineProperty(reopenedScroller, 'clientHeight', { value: 260, configurable: true });
    Object.defineProperty(reopenedScroller, 'scrollTop', { value: 40, configurable: true });

    // 关键断言：重开后再触底应当能真的发出第 4 次请求——若 loadingMore 在重开时
    // 没被清空，这里会被 loadNextPage 的守卫挡住，永远停在 3 次
    getTopslotHistory.mockResolvedValueOnce({
      success: true, total: 5, items: [item('2026-07-08', 3)],
    });
    fireEvent.scroll(reopenedScroller);
    await waitFor(() => expect(getTopslotHistory).toHaveBeenCalledTimes(4));
  });
});
