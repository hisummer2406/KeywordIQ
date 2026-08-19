import { Alert, Button, Input, Modal, Select, Space, Typography, message } from 'antd';
import { useEffect, useState } from 'react';
import { addItems, createGroup, listGroups, type KeywordGroup } from '@/services/library';

/**
 * 把选中的关键词加入词库。
 *
 * **只列当前作用域站点的分组**：分组绑定站点，列出其他站点的分组只会让用户选到一个
 * 注定被服务端拒绝的目标（422）。当前站点还没有分组时，弹窗里就能直接建一个——否则
 * 用户得先跳到词库屏建组、回来重新勾选一遍那几行。
 *
 * 完成后回传「新增 N 个、已存在 M 个」。只说「成功」会让用户以为 20 个词都是新加的，
 * 而实际可能 18 个早就在组里了。
 */

interface Props {
  open: boolean;
  /** 生效作用域的站点。跟着数据走，不是草稿——草稿站点变了但没查时，表格里还是旧站点的行。 */
  site?: string;
  keywordIds: number[];
  onClose: () => void;
  /** 加入成功后回调，用于清空选中。 */
  onAdded: () => void;
}

export default function AddToLibraryModal({ open, site, keywordIds, onClose, onAdded }: Props) {
  const [groups, setGroups] = useState<KeywordGroup[]>([]);
  const [groupId, setGroupId] = useState<number | undefined>();
  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !site) return;
    listGroups(site)
      .then((list) => {
        setGroups(list);
        setGroupId(list[0]?.group_id);
      })
      .catch(() => undefined);
  }, [open, site]);

  const onCreate = async () => {
    const name = newName.trim();
    if (!name || !site) return;
    setLoading(true);
    try {
      const created = await createGroup(site, name);
      setGroups((list) => [...list, created]);
      setGroupId(created.group_id);
      setNewName('');
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责，这里只保留输入内容供重试。
      // **catch 不能省**：见 onOk 上的说明。
    } finally {
      setLoading(false);
    }
  };

  const onOk = async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const result = await addItems(groupId, keywordIds);
      const existedText = result.existed ? `，${result.existed} 个已在组内` : '';
      message.success(`已加入 ${result.added} 个词${existedText}`);
      onAdded();
      onClose();
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责；这里只保证弹窗不关、选中不清，
      // 用户可以直接重试。
      //
      // **这个 catch 不是可有可无的。** antd 的 Modal 不消费 onOk 的返回值（React 的
      // 事件处理器同样不消费），所以少了它，这个 async 函数的拒绝没有任何人接——
      // 浏览器里是一条 unhandled rejection，vitest 里则是「用例全绿但进程退出码非 0」，
      // 后者会拦下所有人的前端提交，且报错指向测试文件而不是这里。
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      open={open}
      title={`加入词库（${keywordIds.length} 个词）`}
      okText="加入"
      cancelText="取消"
      confirmLoading={loading}
      okButtonProps={{ disabled: !groupId }}
      onOk={onOk}
      onCancel={onClose}
    >
      <Typography.Paragraph type="secondary">
        只能加入 <strong>{site}</strong> 站点的分组——分组绑定站点，词也按站点区分。
      </Typography.Paragraph>

      {groups.length === 0 ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`${site} 站点还没有分组，先建一个`}
        />
      ) : (
        <Select
          style={{ width: '100%', marginBottom: 12 }}
          value={groupId}
          onChange={setGroupId}
          placeholder="选择分组"
          options={groups.map((g) => ({
            label: `${g.name}（${g.item_count}）`,
            value: g.group_id,
          }))}
        />
      )}

      <Space.Compact style={{ width: '100%' }}>
        <Input
          value={newName}
          maxLength={50}
          placeholder="新建分组，如「主推词」"
          onChange={(e) => setNewName(e.target.value)}
          onPressEnter={onCreate}
        />
        <Button onClick={onCreate} disabled={!newName.trim() || !site} loading={loading}>
          新建
        </Button>
      </Space.Compact>
    </Modal>
  );
}
