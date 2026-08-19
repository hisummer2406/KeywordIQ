import { DeleteOutlined, EditOutlined } from '@ant-design/icons';
import {
  Button,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useState } from 'react';
import { formatSiteLabel } from '@/pages/Analysis/siteLabel';
import { createGroup, deleteGroup, renameGroup, type KeywordGroup } from '@/services/library';
import type { SiteOption } from '@/services/upload';

/**
 * 分组管理弹窗：新建、重命名、删除。
 *
 * **管理动作放进弹窗，不占主屏横向空间。** 盯盘表格要 1688px，而侧栏形态下右侧只剩
 * 1292px（实测），近 400px 被裁在横向滚动里；分组一览换不来这个代价——用户绝大多数
 * 时候只是在看某一个分组，不是在管理分组。
 *
 * **站点只在新建时选，建完不可改**：分组的全部条目都以站点为前提，改站点等于把内容
 * 整个换掉，那不如新建一个组。
 */

interface Props {
  open: boolean;
  groups: KeywordGroup[];
  sites: SiteOption[];
  onClose: () => void;
  /** 增删改之后回调，让主屏重新拉分组列表；带上要选中的分组 id（删掉当前分组时为空）。 */
  onChanged: (preferId?: number) => void;
}

export default function ManageGroupsModal({ open, groups, sites, onClose, onChanged }: Props) {
  const [newSite, setNewSite] = useState<string | undefined>();
  const [newName, setNewName] = useState('');
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const site = newSite ?? sites[0]?.code;

  const onCreate = async () => {
    const name = newName.trim();
    if (!name || !site) return;
    setBusy(true);
    try {
      const created = await createGroup(site, name);
      setNewName('');
      message.success(`已新建分组「${created.name}」`);
      onChanged(created.group_id);
    } finally {
      setBusy(false);
    }
  };

  const onRename = async () => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!name) return;
    setBusy(true);
    try {
      await renameGroup(editing.id, name);
      setEditing(null);
      onChanged(editing.id);
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (group: KeywordGroup) => {
    setBusy(true);
    try {
      await deleteGroup(group.group_id);
      message.success(`已删除分组「${group.name}」`);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} title="管理分组" footer={null} onCancel={onClose} width={520}>
      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Select
          style={{ width: 160 }}
          value={site}
          onChange={setNewSite}
          placeholder="站点"
          options={sites.map((s) => ({ label: formatSiteLabel(s), value: s.code }))}
        />
        <Input
          value={newName}
          maxLength={50}
          placeholder="新分组名，如「主推词」"
          onChange={(e) => setNewName(e.target.value)}
          onPressEnter={onCreate}
        />
        <Button type="primary" loading={busy} disabled={!newName.trim() || !site} onClick={onCreate}>
          新建
        </Button>
      </Space.Compact>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        分组绑定站点，<strong>建后不可修改站点</strong>；同一批词要盯多个站点就各建一个分组。
      </Typography.Paragraph>

      <List
        size="small"
        dataSource={groups}
        locale={{
          emptyText: <Empty description="还没有分组" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
        }}
        renderItem={(group) => (
          <List.Item>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', minWidth: 0 }}>
              <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>{group.site}</Tag>
              {editing?.id === group.group_id ? (
                <Input
                  autoFocus
                  size="small"
                  value={editing.name}
                  maxLength={50}
                  style={{ flex: 1, minWidth: 0 }}
                  onChange={(e) => setEditing({ id: group.group_id, name: e.target.value })}
                  onPressEnter={onRename}
                  onBlur={onRename}
                />
              ) : (
                <Typography.Text ellipsis style={{ flex: 1, minWidth: 0 }} title={group.name}>
                  {group.name}
                </Typography.Text>
              )}
              <Typography.Text type="secondary" style={{ flexShrink: 0 }}>
                ({group.item_count})
              </Typography.Text>
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                title="重命名"
                onClick={() => setEditing({ id: group.group_id, name: group.name })}
              />
              {/*
                删组连带删掉全部条目且不可撤销，故这里**要**二次确认——与分析屏
                「重置条件」那种只影响一份表单的按钮不是一回事。
              */}
              <Popconfirm
                title={`删除「${group.name}」`}
                description={`组内 ${group.item_count} 个词会一并删除，且不可撤销。`}
                okText="删除"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={() => onDelete(group)}
              >
                <Button type="text" size="small" danger icon={<DeleteOutlined />} title="删除分组" />
              </Popconfirm>
            </div>
          </List.Item>
        )}
      />
    </Modal>
  );
}
