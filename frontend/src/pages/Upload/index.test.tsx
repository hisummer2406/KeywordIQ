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
