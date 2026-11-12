import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { previewImport, commitImport } = vi.hoisted(() => ({
  previewImport: vi.fn(),
  commitImport: vi.fn(),
}));

vi.mock('@/services/competitor', () => ({ previewImport, commitImport }));

import ImportModal from './ImportModal';

const GROUPS = [
  { group_id: 1, name: '头部竞对', watch_count: 2, created_at: '', updated_at: '' },
];
const SITES = [{ code: 'US', name_zh: '美国', domain: 'amazon.com' }];

const PREVIEW = {
  preview_token: 'tok-1',
  expires_at: '2026-08-29T00:00:00Z',
  parsed: 2,
  new: 2,
  existing: 0,
  unparsable: [{ text: 'https://www.amazon.com/stores/Brand/page/x', reason: '品牌店铺页无法提取卖家 ID' }],
  groups: ['文件夹A'],
  samples: [
    { seller_id: 'A1TZ946F8HZTNY', site: 'US', is_new: true, group_name: '文件夹A' },
    { seller_id: 'A1TZ946F8HZTNZ', site: 'US', is_new: false, group_name: null },
  ],
};

function setup(props: Partial<React.ComponentProps<typeof ImportModal>> = {}) {
  const onClose = vi.fn();
  const onImported = vi.fn();
  render(
    <ImportModal
      open
      groups={GROUPS}
      sites={SITES}
      onClose={onClose}
      onImported={onImported}
      {...props}
    />,
  );
  return { onClose, onImported };
}

async function fillTextAndPreview() {
  const textarea = screen.getByPlaceholderText(/一行一个链接或卖家 ID/);
  fireEvent.change(textarea, { target: { value: 'A1TZ946F8HZTNY' } });
  // antd 对纯两字中文按钮文案会插入一个视觉空格(「预览」渲染成「预 览」),按钮名要用
  // 正则匹配,不能按字面量比对。
  const previewButton = screen.getByRole('button', { name: /预\s*览/ });
  await act(async () => {
    fireEvent.click(previewButton);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  previewImport.mockResolvedValue(PREVIEW);
  commitImport.mockResolvedValue({
    created_sellers: 2, created_watches: 2, updated_watches: 0, removed_watches: 0, watch_count: 2,
  });
});

describe('预览结果展示', () => {
  it('展示解析统计、解析失败列表与抽样', async () => {
    setup();
    await fillTextAndPreview();

    await waitFor(() => expect(screen.getByText('2 个卖家')).toBeTruthy());
    // 新增/已存在各自的计数(与「解析出 2 个卖家」共用「2 个」子串,故按精确文本区分)
    expect(screen.getAllByText('2 个').length).toBeGreaterThan(0);
    // 解析失败:原文 + 原因,不是静默丢弃
    expect(screen.getByText(/1 条无法解析/)).toBeTruthy();
    expect(screen.getByText('品牌店铺页无法提取卖家 ID')).toBeTruthy();
    // 抽样表:新增/已存在两种状态都出现(「新增」同时是 Descriptions 的标签,故用 getAllByText)
    expect(screen.getAllByText('新增').length).toBeGreaterThan(1);
    expect(screen.getAllByText('已关注').length).toBeGreaterThan(0);
  });

  it('候选分组来自书签文件夹,预览阶段只展示不落库', async () => {
    setup();
    await fillTextAndPreview();
    // 「文件夹A」同时出现在候选分组标签与抽样表的「候选分组」列,两处都在预期之内
    await waitFor(() => expect(screen.getAllByText('文件夹A').length).toBeGreaterThan(1));
    expect(previewImport).toHaveBeenCalledTimes(1);
    expect(commitImport).not.toHaveBeenCalled();
  });
});

describe('append/replace_group 两种模式的文案', () => {
  it('默认 append 模式:提示未识别分组的归属,不提清空', async () => {
    setup();
    await fillTextAndPreview();
    await waitFor(() => expect(screen.getByText('追加(默认)')).toBeTruthy());
    expect(screen.getByText(/未识别出候选分组的卖家归入/)).toBeTruthy();
    expect(screen.queryByText(/现有的全部关注会被清空/)).toBeNull();
  });

  it('切到 replace_group 模式:提示清空重建,且必须选分组', async () => {
    setup();
    await fillTextAndPreview();
    await waitFor(() => expect(screen.getByText('对齐分组(清空重建)')).toBeTruthy());

    fireEvent.click(screen.getByText('对齐分组(清空重建)'));
    await waitFor(() => expect(screen.getByText(/现有的全部关注会被清空/)).toBeTruthy());

    // 没选目标分组时,确认按钮必须禁用——不能对着一个不存在的分组清空重建
    const confirmButton = screen.getByRole('button', { name: '确认导入' });
    expect(confirmButton).toBeDisabled();
  });
});

/**
 * 回归 issue #59：书签导入按文件夹名自动建组，一份真实书签一次建出 37 个组，名字多是
 * 「7.17」「竞6.12」这类短名。replace_group 选中的组会被整组清空重建，靠滚动在相似
 * 名字里找目标，选错的代价是清空另一个分组。
 */
describe('目标分组下拉可搜索', () => {
  const MANY_GROUPS = ['7.17', '6.24', '竞6.12', '空白', '头部竞对'].map((name, i) => ({
    group_id: i + 1,
    name,
    watch_count: 0,
    created_at: '',
    updated_at: '',
  }));

  /** 打开下拉并输入关键词，返回下拉里当前可见的选项文本。 */
  async function searchIn(placeholder: string, keyword: string) {
    const holder = screen.getByText(placeholder).closest('.ant-select') as HTMLElement;
    fireEvent.mouseDown(holder);
    fireEvent.change(holder.querySelector('input')!, { target: { value: keyword } });
    await waitFor(() =>
      expect(document.querySelectorAll('.ant-select-item-option-content').length).toBeGreaterThan(0),
    );
    return Array.from(document.querySelectorAll('.ant-select-item-option-content')).map(
      (el) => el.textContent,
    );
  }

  it('replace_group：按分组名过滤，不相干的组不再出现在候选里', async () => {
    setup({ groups: MANY_GROUPS });
    await fillTextAndPreview();

    fireEvent.click(screen.getByText('对齐分组(清空重建)'));
    await waitFor(() => expect(screen.getByText(/现有的全部关注会被清空/)).toBeTruthy());

    const visible = await searchIn('选择分组', '6.24');
    expect(visible).toContain('6.24');
    // 过滤要按 label 走。options 形态下 antd 默认按 value（这里是 group_id）过滤，
    // 表现是「搜什么都搜不到」且不报错——所以这条断言守的是 optionFilterProp。
    expect(visible).not.toContain('7.17');
  });

  it('append 的归入分组下拉同样可搜索（同一份分组清单）', async () => {
    setup({ groups: MANY_GROUPS });
    await fillTextAndPreview();

    const visible = await searchIn('不分组', '竞');
    expect(visible).toContain('竞6.12');
    expect(visible).not.toContain('空白');
  });
});

describe('超配额等预览失败', () => {
  it('预览失败时留在输入步,不崩溃(errorHandler 负责提示)', async () => {
    previewImport.mockRejectedValue(new Error('关注总数上限为 3000 个'));
    setup();
    await fillTextAndPreview();

    // 停留在输入步:粘贴文本的 tab 仍然可见,没有进入预览步的统计面板
    await waitFor(() => expect(screen.getByPlaceholderText(/一行一个链接或卖家 ID/)).toBeTruthy());
    expect(screen.queryByText('解析出')).toBeNull();
  });
});
