import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GroupBar from './GroupBar';

/**
 * 分组管理区的用例。
 *
 * 这一块的存在理由是横向宽度：它把分组一览从侧栏搬到表格上方，代价从"吃掉表格 400px 宽"
 * 换成"多占几行纵向"。所以这里也守着一条形态约定——**管理动作就在这一块里**，不再藏进
 * 二级入口。
 */

const GROUPS = [
  {
    group_id: 7,
    site: 'US',
    name: '主推词',
    item_count: 8,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  },
  {
    group_id: 8,
    site: 'DE',
    name: '竞品词',
    item_count: 3,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  },
];

const SITES = [
  { code: 'US', name_zh: '美国', domain: 'amazon.com' },
  { code: 'DE', name_zh: '德国', domain: 'amazon.de' },
];

function setup(groups = GROUPS) {
  const handlers = {
    onSelect: vi.fn(),
    onChanged: vi.fn(),
    onCreate: vi.fn().mockResolvedValue({ ...GROUPS[0], group_id: 9, name: '新组', item_count: 0 }),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
  };
  render(<GroupBar groups={groups} sites={SITES as never} activeId={7} {...handlers} />);
  return handlers;
}

async function openActions() {
  fireEvent.click(screen.getByRole('button', { name: '当前分组操作' }));
  await screen.findByText('重命名');
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('分组管理区', () => {
  it('每个分组都显示站点与词数', async () => {
    setup();
    // 站点必须露出来：这一屏没有站点选择器，站点只能从分组上读到
    expect(screen.getByText('US')).toBeTruthy();
    expect(screen.getByText('DE')).toBeTruthy();
    expect(screen.getByRole('radio', { name: /主推词/ })).toBeTruthy();
    expect(screen.getByRole('radio', { name: /8 词/ })).toBeTruthy();
  });

  it('点分组即切换', async () => {
    const { onSelect } = setup();
    fireEvent.click(screen.getByRole('radio', { name: /竞品词/ }));
    expect(onSelect).toHaveBeenCalledWith(8);
  });

  it('新建后把新分组交回去选中', async () => {
    const { onCreate, onChanged } = setup();

    fireEvent.click(screen.getByRole('button', { name: /新建分组/ }));
    fireEvent.change(screen.getByPlaceholderText(/分组名/), { target: { value: '新组' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    });

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('US', '新组'));
    expect(onChanged).toHaveBeenCalledWith(9);
  });

  it('重命名保留原名做初值，且不碰站点', async () => {
    const { onRename, onChanged } = setup();

    await openActions();
    fireEvent.click(screen.getByText('重命名'));
    const input = screen.getByDisplayValue('主推词');
    fireEvent.change(input, { target: { value: '改过的' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    });

    await waitFor(() => expect(onRename).toHaveBeenCalledWith(7, '改过的'));
    expect(onChanged).toHaveBeenCalledWith(7);
    // 重命名不提供站点选择：改站点等于把整组内容换掉
    expect(screen.queryByText(/建后不可修改站点/)).toBeNull();
  });

  it('删除要二次确认并说明会带走多少个词', async () => {
    const { onDelete } = setup();

    await openActions();
    fireEvent.click(screen.getByText('删除分组'));

    expect(screen.getByText(/组内 8 个词会一并删除/)).toBeTruthy();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('确认删除后不指定选中项，交给主屏回落', async () => {
    const { onDelete, onChanged } = setup();

    await openActions();
    fireEvent.click(screen.getByText('删除分组'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /删\s*除/ }));
    });

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(onChanged).toHaveBeenCalledWith();
  });

  /**
   * 失败路径的两条。断言的是"失败之后界面停在哪"，但它们同时是**未处理拒绝的哨兵**：
   * 这两个 handler 的返回值没有任何人消费（Button 的 onClick、Popconfirm 的 onConfirm），
   * 一旦有人把 `submit` / `remove` 里的 catch 删掉，拒绝就没人接——vitest 会把它算成
   * unhandled error，让整个进程退出码非 0，而用例本身仍然全绿。那种红最难归因，
   * 所以这里留了两条会走到拒绝的路径。
   */
  it('新建失败时保留草稿、不通知主屏——重名是最常见的失败，输入框留着才改得动', async () => {
    const { onCreate, onChanged } = setup();
    onCreate.mockRejectedValue(new Error('已有同名分组'));

    fireEvent.click(screen.getByRole('button', { name: /新建分组/ }));
    fireEvent.change(screen.getByPlaceholderText(/分组名/), { target: { value: '重名的组' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));
    });

    await waitFor(() => expect(onCreate).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue('重名的组')).toBeTruthy();
  });

  it('删除失败时不通知主屏——分组还在，刷新只会让它闪一下又回来', async () => {
    const { onDelete, onChanged } = setup();
    onDelete.mockRejectedValue(new Error('boom'));

    await openActions();
    fireEvent.click(screen.getByText('删除分组'));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /删\s*除/ }));
    });

    await waitFor(() => expect(onDelete).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('一个分组都没有时给出下一步该做什么', async () => {
    setup([]);
    expect(screen.getByText(/还没有分组/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /新建分组/ })).toBeTruthy();
  });

  it('新建时明说站点建后不可改', async () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: /新建分组/ }));
    expect(screen.getByText(/建后不可修改站点/)).toBeTruthy();
  });
});
