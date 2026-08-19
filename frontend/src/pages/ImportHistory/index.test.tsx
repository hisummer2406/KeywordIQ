import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';

const { fetchImportBatches, getSites, retryImportBatch } = vi.hoisted(() => ({
  fetchImportBatches: vi.fn(),
  getSites: vi.fn(),
  retryImportBatch: vi.fn(),
}));

vi.mock('@/services/upload', () => ({ fetchImportBatches, getSites, retryImportBatch }));

import ImportHistoryPage from '@/pages/ImportHistory';

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    site: 'DE',
    period_type: 'WEEK',
    report_date: '2026-07-13',
    file_name: 'DE_热门搜索词_简单_Week_2026_07_13.csv',
    status: 'FAILED',
    total_rows: 3_000_000,
    written_rows: 0,
    processing_seconds: 125,
    error_message: 'COPY 中断：连接被重置',
    created_at: '2026-07-13 03:00:00',
    completed_at: null,
    source_retained: true,
    ...overrides,
  };
}

async function renderWith(rows: ReturnType<typeof row>[]) {
  fetchImportBatches.mockResolvedValue({ data: rows, total: rows.length, success: true });
  await act(async () => {
    render(
      <App>
        <ImportHistoryPage />
      </App>,
    );
  });
  // 等**行落到 DOM**，不是等请求被调用。下面的用例随后用同步查询找行内按钮，
  // 而"请求发出去了"与"结果渲染完了"之间隔着一次状态更新——负载下这中间会被切开。
  await waitFor(() => expect(fetchImportBatches).toHaveBeenCalled());
  if (rows.length) {
    await screen.findByText(rows[0].file_name as string);
  }
}

/**
 * 表格里某一行的重导按钮。
 *
 * 名字用正则而非全等：按钮带 ReloadOutlined，antd 会把图标的 aria-label 拼进无障碍名
 * （「reload 重导」），全等匹配会在加图标后静默找不到元素——表现是 `toBeEnabled` 报
 * 「received value must be an HTMLElement」，而不是「按钮是禁用的」，很难一眼看懂。
 */
function retryButtonIn(fileName: string) {
  const cell = screen.getByText(fileName);
  const tr = cell.closest('tr') as HTMLElement;
  return within(tr).queryByRole('button', { name: /重导/ });
}

describe('导入历史屏', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSites.mockResolvedValue([
      { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
      { code: 'US', name_zh: '美国', domain: 'amazon.com' },
    ]);
    retryImportBatch.mockResolvedValue({});
  });

  it('展示失败原因——本屏没有详情视图，读不到就无从判断该不该重导', async () => {
    await renderWith([row()]);
    expect(await screen.findByText('COPY 中断：连接被重置')).toBeInTheDocument();
  });

  it('写入行数与源文件行数一并展示，不只给百分比', async () => {
    await renderWith([row({ written_rows: 2_999_400, total_rows: 3_000_000 })]);
    // 99.98% 看不出少了 600 行，而历史场景关心的正是这个差值
    expect(await screen.findByText('2,999,400 / 3,000,000')).toBeInTheDocument();
  });

  it('失败且原始文件仍在的行：重导可点，点击后触发接口并刷新列表', async () => {
    await renderWith([row()]);

    const btn = retryButtonIn('DE_热门搜索词_简单_Week_2026_07_13.csv');
    expect(btn).toBeEnabled();

    fetchImportBatches.mockClear();
    await act(async () => {
      await userEvent.click(btn!);
    });

    expect(retryImportBatch).toHaveBeenCalledWith(1);
    // 重导后必须刷新：新批次记录由后台创建，不刷新用户看不到自己刚触发的那次
    await waitFor(() => expect(fetchImportBatches).toHaveBeenCalled());
  });

  /**
   * 断言的是"重导失败之后界面停在哪"，同时是**未处理拒绝的哨兵**：`handleRetry` 的返回值
   * 没有人消费（Button 的 onClick 不消费 promise），一旦 catch 被删掉，拒绝就没人接——
   * vitest 会让整个进程退出码非 0，而用例仍然全绿，那种红最难归因。
   */
  it('重导失败时不谎报成功、不刷新列表，按钮回到可点', async () => {
    await renderWith([row()]);
    retryImportBatch.mockRejectedValue(new Error('boom'));

    const btn = retryButtonIn('DE_热门搜索词_简单_Week_2026_07_13.csv');
    fetchImportBatches.mockClear();
    await act(async () => {
      await userEvent.click(btn!);
    });

    await waitFor(() => expect(retryImportBatch).toHaveBeenCalledWith(1));
    // 失败提示由 app.tsx 的全局出口负责，这一屏只需不谎报、不刷新
    expect(screen.queryByText(/已触发重导/)).toBeNull();
    expect(fetchImportBatches).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(retryButtonIn('DE_热门搜索词_简单_Week_2026_07_13.csv')).toBeEnabled(),
    );
  });

  it('失败但原始文件未留存的行：重导置灰，不给一个点下去必然报错的按钮', async () => {
    await renderWith([row({ source_retained: false })]);

    const btn = retryButtonIn('DE_热门搜索词_简单_Week_2026_07_13.csv');
    expect(btn).toBeDisabled();

    await act(async () => {
      await userEvent.click(btn!);
    });
    expect(retryImportBatch).not.toHaveBeenCalled();
  });

  it.each(['COMPLETED', 'QUEUED', 'PROCESSING'])(
    '%s 的行不显示重导入口（后端只允许 FAILED）',
    async (status) => {
      await renderWith([row({ status, error_message: '' })]);
      expect(retryButtonIn('DE_热门搜索词_简单_Week_2026_07_13.csv')).toBeNull();
    },
  );

  it('首屏按默认分页请求一次（映射到后端参数名的换算在 services/upload.test.ts 里钉）', async () => {
    await renderWith([row()]);

    expect(fetchImportBatches).toHaveBeenCalledTimes(1);
    const [params] = fetchImportBatches.mock.calls[0];
    expect(params).toMatchObject({ current: 1, pageSize: 20 });
  });

  it('不轮询——盯进度是上传屏看板的职责，这里一次查询只请求一次', async () => {
    // shouldAdvanceTime 让 waitFor / testing-library 的等待照常推进，只有我们手动
    // 快进的那 30 秒是假的；否则渲染阶段的 await 会永远等不到而超时。
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderWith([row()]);
      const callsAfterMount = fetchImportBatches.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });

      expect(fetchImportBatches.mock.calls.length).toBe(callsAfterMount);
    } finally {
      vi.useRealTimers();
    }
  });
});
