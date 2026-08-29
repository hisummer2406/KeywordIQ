import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Card, Input, Modal, Popconfirm, Space, Typography } from 'antd';
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
  const [busy, setBusy] = useState(false);

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
      return;
    }
    // 删组不取消关注,只解除归属——回落到「全部关注」而不是某个具体分组,
    // 因为被删分组下的关注记录此刻已经"未分组"了。
    onChanged();
  };

  return (
    <Card size="small" title="分组" styles={{ body: { padding: 12 } }}>
      <Space size={[8, 8]} wrap>
        <Button
          type={activeId === undefined ? 'primary' : 'default'}
          onClick={() => onSelect(undefined)}
        >
          全部关注
          <Typography.Text
            type={activeId === undefined ? undefined : 'secondary'}
            style={{ marginInlineStart: 6, color: activeId === undefined ? '#fff' : undefined }}
          >
            {totalWatchCount}
          </Typography.Text>
        </Button>

        {groups.map((group) => {
          const active = group.group_id === activeId;
          return (
            <Space.Compact key={group.group_id}>
              <Button
                type={active ? 'primary' : 'default'}
                onClick={() => onSelect(group.group_id)}
              >
                {group.name}
                <Typography.Text
                  type={active ? undefined : 'secondary'}
                  style={{ marginInlineStart: 6, color: active ? '#fff' : undefined }}
                >
                  {group.watch_count}
                </Typography.Text>
              </Button>
              <Button
                icon={<EditOutlined />}
                title={`重命名「${group.name}」`}
                onClick={() =>
                  setDraft({ mode: 'rename', groupId: group.group_id, name: group.name })
                }
              />
              {/* 删组连带解除全部归属（不取消关注),仍需二次确认——不可撤销的表单变更。 */}
              <Popconfirm
                title={`删除「${group.name}」`}
                description={`组内 ${group.watch_count} 个卖家会变为未分组,关注关系本身不受影响。`}
                okText="删除"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={() => remove(group)}
              >
                <Button danger icon={<DeleteOutlined />} title={`删除「${group.name}」`} />
              </Popconfirm>
            </Space.Compact>
          );
        })}

        <Button icon={<PlusOutlined />} onClick={() => setDraft({ mode: 'create', name: '' })}>
          新建分组
        </Button>
      </Space>

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
    </Card>
  );
}
