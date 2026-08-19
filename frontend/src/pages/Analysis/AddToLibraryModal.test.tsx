import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listGroups, createGroup, addItems } = vi.hoisted(() => ({
  listGroups: vi.fn(),
  createGroup: vi.fn(),
  addItems: vi.fn(),
}));

vi.mock('@/services/library', () => ({ listGroups, createGroup, addItems }));

import AddToLibraryModal from './AddToLibraryModal';

/**
 * 弹窗自身的契约测试。
 *
 * **断言的是回调有没有被调用，不是「dialog 节点还在不在」**：antd 的 Modal 关闭后
 * 节点仍留在 DOM 里，且在这套 jsdom 环境下连 `display: none` 都不加（拿一个孤立的原生
 * antd Modal 验过，行为一模一样）。按节点在不在去断言，测的是 antd 的实现细节，而且
 * 会得出「关不掉」这种与事实相反的结论。
 */

const GROUP = {
  group_id: 7,
  site: 'US',
  name: '主推词',
  item_count: 3,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

function setup(props: Partial<Parameters<typeof AddToLibraryModal>[0]> = {}) {
  const onClose = vi.fn();
  const onAdded = vi.fn();
  render(
    <AddToLibraryModal
      open
      site="US"
      keywordIds={[1, 2]}
      onClose={onClose}
      onAdded={onAdded}
      {...props}
    />,
  );
  return { onClose, onAdded };
}

const footerButton = (pattern: RegExp) =>
  Array.from(document.querySelectorAll<HTMLElement>('.ant-modal-footer button')).find((b) =>
    pattern.test(b.textContent ?? ''),
  )!;

beforeEach(() => {
  vi.clearAllMocks();
  listGroups.mockResolvedValue([GROUP]);
  addItems.mockResolvedValue({ added: 2, existed: 0, item_count: 5 });
  createGroup.mockResolvedValue({ ...GROUP, group_id: 9, name: '新组', item_count: 0 });
});

describe('加入词库弹窗', () => {
  it('只按当前站点拉分组', async () => {
    setup();
    await waitFor(() => expect(listGroups).toHaveBeenCalledWith('US'));
  });

  it('点取消要通知父组件关闭', async () => {
    const { onClose } = setup();
    await waitFor(() => expect(listGroups).toHaveBeenCalled());

    await act(async () => {
      fireEvent.click(footerButton(/取\s*消/));
    });

    expect(onClose).toHaveBeenCalled();
  });

  it('加入成功后先清空选中再关闭', async () => {
    const { onClose, onAdded } = setup();
    await waitFor(() => expect(listGroups).toHaveBeenCalled());

    await act(async () => {
      fireEvent.click(footerButton(/加\s*入/));
    });

    await waitFor(() => expect(addItems).toHaveBeenCalledWith(7, [1, 2]));
    expect(onAdded).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('加入失败时不关闭，也不清空选中（用户还要重试）', async () => {
    addItems.mockRejectedValue(new Error('boom'));
    const { onClose, onAdded } = setup();
    await waitFor(() => expect(listGroups).toHaveBeenCalled());

    await act(async () => {
      fireEvent.click(footerButton(/加\s*入/));
    });

    await waitFor(() => expect(addItems).toHaveBeenCalled());
    expect(onAdded).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('该站点没有分组时提示先建一个，且加入按钮不可点', async () => {
    listGroups.mockResolvedValue([]);
    setup();

    await waitFor(() => expect(screen.getByText(/还没有分组，先建一个/)).toBeTruthy());
    expect(footerButton(/加\s*入/)).toBeDisabled();
  });

  it('可以当场新建分组并直接选中它', async () => {
    listGroups.mockResolvedValue([]);
    setup();
    await waitFor(() => expect(listGroups).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText(/新建分组/), { target: { value: '新组' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /新\s*建/ }));
    });

    await waitFor(() => expect(createGroup).toHaveBeenCalledWith('US', '新组'));
    await waitFor(() => expect(footerButton(/加\s*入/)).not.toBeDisabled());
  });
});
