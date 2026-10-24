import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';

const { getProcessingStatus, inspectFilename, ossDirectUploadAndImport } = vi.hoisted(
  () => ({
    getProcessingStatus: vi.fn(),
    inspectFilename: vi.fn(),
    ossDirectUploadAndImport: vi.fn(),
  }),
);

vi.mock('@/services/upload', () => ({ getProcessingStatus, inspectFilename }));
vi.mock('@/services/ossUpload', () => ({ ossDirectUploadAndImport }));

import UploadPage from '@/pages/Upload';

async function renderSettled() {
  await act(async () => {
    render(
      <App>
        <UploadPage />
      </App>,
    );
  });
}

/** 选中文件 = 往 Dragger 隐藏的 file input 上放文件，等价于用户拖入。 */
async function pickFile(name: string) {
  const file = new File(['a,b\n1,2\n'], name, { type: 'text/csv' });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await act(async () => {
    await userEvent.upload(input, file);
  });
  return file;
}

/**
 * 归属三元组（站点 / 周期 / 报告日期）决定数据写进哪张事实表的哪个分区。
 * 这组用例守的是：它由文件名判定、在上传前可见、判定不出时不猜。
 */
describe('数据上传屏的归属判定', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getProcessingStatus.mockResolvedValue({ items: [] });
    ossDirectUploadAndImport.mockResolvedValue({});
  });

  it('文件名可完整判定时零输入直传，站点用文件名判定的那个', async () => {
    inspectFilename.mockResolvedValue({
      filename: 'DE_热门搜索词_简单_Day_2026_07_16.csv',
      site: 'DE',
      period: 'DAY',
      report_date: '2026-07-16',
      needs: [],
    });
    await renderSettled();
    await pickFile('DE_热门搜索词_简单_Day_2026_07_16.csv');

    // 三元组在上传前就摆出来了。限定在回显区域内断言——"日报"在周期单选按钮上也有一个
    const banner = await screen.findByRole('alert');
    expect(within(banner).getByText('DE')).toBeInTheDocument();
    expect(within(banner).getByText('日报')).toBeInTheDocument();
    expect(within(banner).getByText('2026-07-16')).toBeInTheDocument();

    // 界面上没有任何站点/周期选择项，归属全部来自文件名
    await waitFor(() => expect(ossDirectUploadAndImport).toHaveBeenCalled());
    expect(ossDirectUploadAndImport.mock.calls[0][1]).toMatchObject({ site: 'DE', period: 'DAY' });
  });

  it('周期判定不出时拒绝该文件，不猜成周报', async () => {
    inspectFilename.mockResolvedValue({
      filename: 'DE_热门搜索词_2026_07_16.csv',
      site: 'DE',
      period: null,
      report_date: '2026-07-16',
      needs: ['period'],
    });
    await renderSettled();
    await pickFile('DE_热门搜索词_2026_07_16.csv');

    // 同一句话会同时出现在告警面板与全局提示里，故用 getAllByText
    await waitFor(() => expect(screen.getAllByText(/无法从文件名判定归属/).length).toBeGreaterThan(0));
    expect(ossDirectUploadAndImport).not.toHaveBeenCalled();
  });

  it('站点判定不出时拒绝该文件', async () => {
    inspectFilename.mockResolvedValue({
      filename: '热门搜索词_简单_Day_2026_07_16.csv',
      site: null,
      period: 'DAY',
      report_date: '2026-07-16',
      needs: ['site'],
    });
    await renderSettled();
    await pickFile('热门搜索词_简单_Day_2026_07_16.csv');

    // 同一句话会同时出现在告警面板与全局提示里，故用 getAllByText
    await waitFor(() => expect(screen.getAllByText(/无法从文件名判定归属/).length).toBeGreaterThan(0));
    expect(ossDirectUploadAndImport).not.toHaveBeenCalled();
  });

  it('文件名解析失败时不放行（错误提示由全局 errorHandler 负责）', async () => {
    // 用 .csv 结尾：非 CSV 会被 Dragger 的 accept 直接滤掉，根本走不到解析这一步
    inspectFilename.mockRejectedValue(new Error('后端解析失败'));
    await renderSettled();
    await pickFile('DE_坏文件_2026_07_16.csv');

    await waitFor(() => expect(inspectFilename).toHaveBeenCalled());
    expect(ossDirectUploadAndImport).not.toHaveBeenCalled();
  });

  it('同时拖入多个文件时各用各的归属（不被后一个覆盖）', async () => {
    inspectFilename.mockImplementation(async (name: string) =>
      name.startsWith('DE')
        ? { filename: name, site: 'DE', period: 'DAY', report_date: '2026-07-16', needs: [] }
        : { filename: name, site: 'GB', period: 'WEEK', report_date: '2026-07-20', needs: [] },
    );
    await renderSettled();

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      await userEvent.upload(input, [
        new File(['a'], 'DE_热门搜索词_简单_Day_2026_07_16.csv', { type: 'text/csv' }),
        new File(['a'], 'GB_热门搜索词_简单_Week_2026_07_20.csv', { type: 'text/csv' }),
      ]);
    });

    await waitFor(() => expect(ossDirectUploadAndImport).toHaveBeenCalledTimes(2));
    const byName = Object.fromEntries(
      ossDirectUploadAndImport.mock.calls.map((c: any[]) => [c[0].name, c[1]]),
    );
    expect(byName['DE_热门搜索词_简单_Day_2026_07_16.csv']).toMatchObject({ site: 'DE', period: 'DAY' });
    expect(byName['GB_热门搜索词_简单_Week_2026_07_20.csv']).toMatchObject({ site: 'GB', period: 'WEEK' });
  });
});

/**
 * 导入状态列表。后端只回状态码，中文由前端呈现——把展示文案放后端会得到
 * 「PROCESSING 翻成中文、其余原样」这种半拉子映射（历史现状就是如此）。
 */
describe('最近导入状态', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspectFilename.mockResolvedValue({
      filename: 'x.csv', site: 'US', period: 'DAY', report_date: '2026-07-16', needs: [],
    });
  });

  it('状态码翻成中文，排队中说明它会自动开始', async () => {
    getProcessingStatus.mockResolvedValue({
      items: [
        { batch_name: 'a.csv', progress_percent: 0, total_records: 0, status: 'QUEUED',
          error_message: '' },
      ],
    });
    await renderSettled();

    const row = (await screen.findByText('a.csv')).closest('tr')!;
    expect(within(row).getByText('排队中')).toBeInTheDocument();
    // 「等前一个导入结束后自动开始」是这一格的全部意义：不说，用户不知道要不要重传
    expect(within(row).getByText(/自动开始/)).toBeInTheDocument();
    expect(within(row).queryByText('QUEUED')).not.toBeInTheDocument();
  });

  it('失败批次把原因显示出来（否则「请重新上传」这句话没人看得见）', async () => {
    getProcessingStatus.mockResolvedValue({
      items: [
        { batch_name: 'b.csv', progress_percent: 0, total_records: 0, status: 'FAILED',
          error_message: '已有导入正在进行；本地上传无法自动重排，请稍后重新上传' },
      ],
    });
    await renderSettled();

    const row = (await screen.findByText('b.csv')).closest('tr')!;
    expect(within(row).getByText('失败')).toBeInTheDocument();
    expect(within(row).getByText(/请稍后重新上传/)).toBeInTheDocument();
  });

  it('成功与处理中各有自己的文案', async () => {
    getProcessingStatus.mockResolvedValue({
      items: [
        { batch_name: 'c.csv', progress_percent: 100, total_records: 12, status: 'COMPLETED',
          error_message: '' },
        { batch_name: 'd.csv', progress_percent: 30, total_records: 10, status: 'PROCESSING',
          error_message: '' },
      ],
    });
    await renderSettled();

    expect(within((await screen.findByText('c.csv')).closest('tr')!).getByText('已完成'))
      .toBeInTheDocument();
    expect(within((await screen.findByText('d.csv')).closest('tr')!).getByText('处理中'))
      .toBeInTheDocument();
  });

  it('已完成但查询统计待更新/失败时说明可能较慢；就绪与旧批次不打扰', async () => {
    getProcessingStatus.mockResolvedValue({
      items: [
        { batch_name: 'p.csv', progress_percent: 100, total_records: 1, status: 'COMPLETED',
          error_message: '', stats_status: 'PENDING' },
        { batch_name: 'f.csv', progress_percent: 100, total_records: 1, status: 'COMPLETED',
          error_message: '', stats_status: 'FAILED' },
        { batch_name: 'r.csv', progress_percent: 100, total_records: 1, status: 'COMPLETED',
          error_message: '', stats_status: 'READY' },
        { batch_name: 'o.csv', progress_percent: 100, total_records: 1, status: 'COMPLETED',
          error_message: '', stats_status: null },
      ],
    });
    await renderSettled();

    const row = async (name: string) => (await screen.findByText(name)).closest('tr')!;
    expect(within(await row('p.csv')).getByText(/查询统计待更新/)).toBeInTheDocument();
    expect(within(await row('f.csv')).getByText(/请联系管理员/)).toBeInTheDocument();
    expect(within(await row('r.csv')).queryByText(/查询统计/)).not.toBeInTheDocument();
    expect(within(await row('o.csv')).queryByText(/查询统计/)).not.toBeInTheDocument();
  });
});
