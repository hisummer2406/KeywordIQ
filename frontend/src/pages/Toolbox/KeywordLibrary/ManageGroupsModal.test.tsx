import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { createGroup, renameGroup, deleteGroup } = vi.hoisted(() => ({
  createGroup: vi.fn(),
  renameGroup: vi.fn(),
  deleteGroup: vi.fn(),
}));

vi.mock('@/services/library', () => ({ createGroup, renameGroup, deleteGroup }));

import ManageGroupsModal from './ManageGroupsModal';

const GROUP = {
  group_id: 7,
  site: 'US',
  name: '主推词',
  item_count: 3,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const SITES = [
  { code: 'US', name_zh: '美国', domain: 'amazon.com' },
  { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
];

function setup(groups = [GROUP]) {
  const onChanged = vi.fn();
  const onClose = vi.fn();
  render(
    <ManageGroupsModal
      open
      groups={groups}
      sites={SITES as never}
      onClose={onClose}
      onChanged={onChanged}
    />,
  );
  return { onChanged, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  createGroup.mockResolvedValue({ ...GROUP, group_id: 9, name: '新组', item_count: 0 });
  renameGroup.mockResolvedValue({ ...GROUP, name: '改过的' });
  deleteGroup.mockResolvedValue({ success: true });
});

describe('管理分组弹窗', () => {
  it('新建后把新分组的 id 交回主屏选中', async () => {
    const { onChanged } = setup();

    fireEvent.change(screen.getByPlaceholderText(/新分组名/), { target: { value: '新组' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /新\s*建/ }));
    });

    await waitFor(() => expect(createGroup).toHaveBeenCalledWith('US', '新组'));
    expect(onChanged).toHaveBeenCalledWith(9);
  });

  it('删除后不指定选中项，让主屏回落到第一个分组', async () => {
    const { onChanged } = setup();

    await act(async () => {
      fireEvent.click(screen.getByTitle('删除分组'));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /删\s*除/ }));
    });

    await waitFor(() => expect(deleteGroup).toHaveBeenCalledWith(7));
    expect(onChanged).toHaveBeenCalledWith();
  });

  it('删除要二次确认并说明会带走多少个词', async () => {
    setup();

    await act(async () => {
      fireEvent.click(screen.getByTitle('删除分组'));
    });

    expect(screen.getByText(/组内 3 个词会一并删除/)).toBeTruthy();
    expect(deleteGroup).not.toHaveBeenCalled();
  });

  it('重命名走 PATCH，且不碰站点', async () => {
    const { onChanged } = setup();

    await act(async () => {
      fireEvent.click(screen.getByTitle('重命名'));
    });
    const input = screen.getByDisplayValue('主推词');
    fireEvent.change(input, { target: { value: '改过的' } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
      fireEvent.keyPress(input, { key: 'Enter', charCode: 13 });
    });

    await waitFor(() => expect(renameGroup).toHaveBeenCalledWith(7, '改过的'));
    expect(onChanged).toHaveBeenCalledWith(7);
  });

  it('明说站点建后不可改', async () => {
    setup();
    expect(screen.getByText(/建后不可修改站点/)).toBeTruthy();
  });

  it('一个分组都没有时给空提示', async () => {
    setup([]);
    expect(screen.getByText('还没有分组')).toBeTruthy();
  });
});
