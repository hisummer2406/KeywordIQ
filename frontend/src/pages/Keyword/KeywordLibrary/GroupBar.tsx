import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Card, Input, Modal, Popconfirm, Select, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import { formatSiteLabel } from '@/pages/Analysis/siteLabel';
import type { KeywordGroup } from '@/services/library';
import type { SiteOption } from '@/services/upload';

/**
 * 分组管理区：独立一块，横在盯盘表格上方。
 *
 * **占纵向空间，不占横向空间。** 这一屏的横向是稀缺资源——盯盘表格要 1688px，而
 * 1920 视口下整屏也只有 1584px 可用；曾经的左侧栏形态把可视区压到 1292px，近 400px 被裁
 * 进横向滚动（实测）。分组一览本身是有价值的（一眼看到有哪些清单、各有多少词），
 * 于是把它摆成一条横向排布的芯片区：多几行是纵向的代价，表格宽度分毫不让。
 *
 * 选中、重命名、删除、新建都在这一块里完成，**不再藏进弹窗**——除了改名字需要一个输入框，
 * 那是个一次性的小对话框，不是管理入口本身。
 */

interface Props {
  groups: KeywordGroup[];
  sites: SiteOption[];
  activeId?: number;
  onSelect: (groupId: number) => void;
  /** 增删改之后回调，让主屏重新拉分组；带上要选中的分组 id（删掉当前分组时为空）。 */
  onChanged: (preferId?: number) => void;
  onCreate: (site: string, name: string) => Promise<KeywordGroup>;
  onRename: (groupId: number, name: string) => Promise<unknown>;
  onDelete: (group: KeywordGroup) => Promise<unknown>;
}

type NameDraft =
  | { mode: 'create'; site: string; name: string }
  | { mode: 'rename'; groupId: number; name: string };

export default function GroupBar({
  groups,
  sites,
  activeId,
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
        const created = await onCreate(draft.site, name);
        onChanged(created.group_id);
      } else {
        await onRename(draft.groupId, name);
        onChanged(draft.groupId);
      }
      setDraft(null);
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责，这里只保留草稿供重试
      // （重名是这条路径最常见的失败，输入框留着才改得动）。
      // **catch 不能省**：Button 的 onClick 不消费返回的 promise，少了它就是一条
      // unhandled rejection——浏览器里进控制台，vitest 里让整个进程退出码非 0。
    } finally {
      setBusy(false);
    }
  };

  const remove = async (group: KeywordGroup) => {
    try {
      await onDelete(group);
    } catch {
      // 同上：删除失败时不通知主屏刷新（分组还在），提示交给全局出口。
      return;
    }
    // 不指定选中项：删掉的可能正是当前分组，交给主屏回落到第一个。
    onChanged();
  };

  return (
    <Card size="small" title="分组" styles={{ body: { padding: 12 } }}>
      {groups.length === 0 && (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          还没有分组。新建一个，再到「ABA数据选品」勾选结果行加入词库。
        </Typography.Paragraph>
      )}

      {/* 新建按钮跟在芯片流的末尾，不单独占一行——这一块越矮，表格越早开始。 */}
      <Space size={[8, 8]} wrap>
        {groups.map((group) => {
          const active = group.group_id === activeId;
          return (
            <Space.Compact key={group.group_id}>
              <Button
                type={active ? 'primary' : 'default'}
                onClick={() => onSelect(group.group_id)}
              >
                <Tag
                  color={active ? 'blue' : 'default'}
                  style={{ marginInlineEnd: 4, borderColor: 'transparent' }}
                >
                  {group.site}
                </Tag>
                {group.name}
                <Typography.Text
                  type={active ? undefined : 'secondary'}
                  style={{ marginInlineStart: 6, color: active ? '#fff' : undefined }}
                >
                  {group.item_count}
                </Typography.Text>
              </Button>
              <Button
                icon={<EditOutlined />}
                title={`重命名「${group.name}」`}
                onClick={() =>
                  setDraft({ mode: 'rename', groupId: group.group_id, name: group.name })
                }
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
                onConfirm={() => remove(group)}
              >
                <Button danger icon={<DeleteOutlined />} title={`删除「${group.name}」`} />
              </Popconfirm>
            </Space.Compact>
          );
        })}
        <Button
          icon={<PlusOutlined />}
          disabled={!sites.length}
          onClick={() => setDraft({ mode: 'create', site: sites[0]?.code ?? '', name: '' })}
        >
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
        {draft?.mode === 'create' && (
          <>
            <Select
              style={{ width: '100%', marginBottom: 8 }}
              value={draft.site}
              onChange={(site) => setDraft({ ...draft, site })}
              options={sites.map((s) => ({ label: formatSiteLabel(s), value: s.code }))}
            />
            <Typography.Paragraph type="secondary">
              分组绑定站点，<strong>建后不可修改站点</strong>；同一批词要盯多个站点就各建一个分组。
            </Typography.Paragraph>
          </>
        )}
        <Input
          autoFocus
          value={draft?.name ?? ''}
          maxLength={50}
          placeholder="分组名，如「主推词」"
          onChange={(e) => draft && setDraft({ ...draft, name: e.target.value })}
          onPressEnter={submit}
        />
      </Modal>
    </Card>
  );
}
