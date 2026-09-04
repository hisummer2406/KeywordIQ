import { MoreOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Card, Dropdown, Input, Modal, Radio, Space, Typography } from 'antd';
import { useState } from 'react';
import type { CompetitorGroup } from '@/services/competitor';

/**
 * 分组管理区：独立一块，横在主表格上方，不做侧栏——理由与「我的词库」`GroupBar` 相同
 * （表格横向是稀缺资源，纵向可以滚，横向裁掉的列找不回来，见 spec.md「分组管理不得占用
 * 主表格的横向宽度」）。
 *
 * **与词库 GroupBar 的关键差异：不绑定站点。** 分组这里只是运营自己的归类习惯，一个分组
 * 内允许出现 US/GB/DE 混合的卖家（design.md 决策 1）。因此新建分组没有站点选择，也没有
 * `activeId=undefined`（"全部关注"）之外的隐含约束。
 *
 * `activeId === undefined` 是一个恒定存在、不可删除的伪分组「全部关注」，不在 `groups`
 * 数组里——它对应主表格查询的 `group_id: null`（后端语义是"不按分组过滤"）。
 */

interface Props {
  groups: CompetitorGroup[];
  activeId: number | undefined;
  totalWatchCount: number;
  onSelect: (groupId: number | undefined) => void;
  onChanged: (preferId?: number) => void;
  onCreate: (name: string) => Promise<CompetitorGroup>;
  onRename: (groupId: number, name: string) => Promise<unknown>;
  onDelete: (group: CompetitorGroup) => Promise<unknown>;
}

type NameDraft =
  | { mode: 'create'; name: string }
  | { mode: 'rename'; groupId: number; name: string };

export default function GroupBar({
  groups,
  activeId,
  totalWatchCount,
  onSelect,
  onChanged,
  onCreate,
  onRename,
  onDelete,
}: Props) {
  const [draft, setDraft] = useState<NameDraft | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<CompetitorGroup | null>(null);
  const [busy, setBusy] = useState(false);
  const activeGroup = groups.find((group) => group.group_id === activeId);

  const submit = async () => {
    if (!draft) return;
    const name = draft.name.trim();
    if (!name) return;
    setBusy(true);
    try {
      if (draft.mode === 'create') {
        const created = await onCreate(name);
        onChanged(created.group_id);
      } else {
        await onRename(draft.groupId, name);
        onChanged(draft.groupId);
      }
      setDraft(null);
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责,这里只保留草稿供重试
      // （重名是这条路径最常见的失败,输入框留着才改得动）。
    } finally {
      setBusy(false);
    }
  };

  const remove = async (group: CompetitorGroup) => {
    try {
      await onDelete(group);
    } catch {
      return false;
    }
    // 删组不取消关注,只解除归属——回落到「全部关注」而不是某个具体分组,
    // 因为被删分组下的关注记录此刻已经"未分组"了。
    onChanged();
    return true;
  };

  const groupMenu = (group: CompetitorGroup) => ({
    items: [
      { key: 'rename', label: '重命名' },
      { type: 'divider' as const },
      { key: 'delete', label: <Typography.Text type="danger">删除分组</Typography.Text> },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'rename') {
        setDraft({ mode: 'rename', groupId: group.group_id, name: group.name });
      } else if (key === 'delete') {
        setDeleteTarget(group);
      }
    },
  });

  return (
    <Card
      size="small"
      title={
        <Space size={8}>
          <span>分组</span>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            全部关注与自定义清单
          </Typography.Text>
        </Space>
      }
      extra={
        <Space size={4}>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setDraft({ mode: 'create', name: '' })}>新建分组</Button>
          {activeGroup && (
            <Dropdown menu={groupMenu(activeGroup)} trigger={['click']}>
              <Button type="text" icon={<MoreOutlined />} aria-label="当前分组操作" />
            </Dropdown>
          )}
        </Space>
      }
      styles={{ body: { padding: 10 } }}
    >
      <Radio.Group
        value={activeId ?? 0}
        onChange={(event) => onSelect(event.target.value === 0 ? undefined : event.target.value)}
        optionType="button"
        buttonStyle="solid"
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}
      >
        <Radio.Button value={0}>全部关注 · {totalWatchCount} 位卖家</Radio.Button>
        {groups.map((group) => (
          <Radio.Button key={group.group_id} value={group.group_id}>
            {group.name} · {group.watch_count} 位卖家
          </Radio.Button>
        ))}
      </Radio.Group>

      <Modal
        open={draft !== null}
        title={draft?.mode === 'rename' ? '重命名分组' : '新建分组'}
        okText="保存"
        cancelText="取消"
        confirmLoading={busy}
        okButtonProps={{ disabled: !draft?.name.trim() }}
        onOk={submit}
        onCancel={() => setDraft(null)}
      >
        <Input
          autoFocus
          value={draft?.name ?? ''}
          maxLength={50}
          placeholder="分组名,如「头部竞对」"
          onChange={(e) => draft && setDraft({ ...draft, name: e.target.value })}
          onPressEnter={submit}
        />
        {draft?.mode === 'create' && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            分组不绑定站点,同一个分组里可以混着放 US/GB/DE 各站点的卖家。
          </Typography.Paragraph>
        )}
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title={`删除「${deleteTarget?.name ?? ''}」`}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        onOk={async () => {
          if (deleteTarget && (await remove(deleteTarget))) setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      >
        组内 {deleteTarget?.watch_count ?? 0} 个卖家会变为未分组，关注关系本身不受影响。
      </Modal>
    </Card>
  );
}
