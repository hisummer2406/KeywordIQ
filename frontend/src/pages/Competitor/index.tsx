import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Button, Card, Progress, Space, Typography, message } from 'antd';
import { useEffect, useRef, useState } from 'react';
import {
  createGroup,
  deleteGroup,
  listGroups,
  listWatches,
  removeWatches,
  renameGroup,
  updateWatch,
  type CompetitorGroup,
  type WatchRow,
} from '@/services/competitor';
import { getSites, type SiteOption } from '@/services/upload';
import GroupBar from './GroupBar';
import ImportModal from './ImportModal';
import { buildWatchColumns } from './columns';

/**
 * 竞对监控 · 关注清单（issue #56 P0）。只有这一份清单——分组、导入、状态跟踪；
 * 数据获取层（浏览器插件/Keepa）与「今日上新」页不在本变更范围，见 design.md。
 *
 * **进屏即查**（与「持续增长市场」屏相反、与「我的词库」屏一致）：这一屏的空表格恒与
 * "还没做选择"无关——行集合就是用户自己的关注清单。
 *
 * **没有站点选择器**：分组不绑定站点（与词库屏的关键差异），主表格改由 `group_id`
 * （可空,undefined 表示"全部关注"）驱动。
 */
export default function CompetitorPage() {
  const actionRef = useRef<ActionType>();

  const [sites, setSites] = useState<SiteOption[]>([]);
  const [groups, setGroups] = useState<CompetitorGroup[]>([]);
  const [activeGroupId, setActiveGroupId] = useState<number | undefined>(undefined);

  const [watchCount, setWatchCount] = useState(0);
  const [maxWatch, setMaxWatch] = useState(0);

  const [importOpen, setImportOpen] = useState(false);

  useEffect(() => {
    getSites()
      .then(setSites)
      .catch(() => undefined);
    listGroups()
      .then(setGroups)
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reloadGroups = async () => {
    const list = await listGroups().catch(() => undefined);
    if (list) setGroups(list);
  };

  const switchGroup = (groupId: number | undefined) => {
    setActiveGroupId(groupId);
    actionRef.current?.reload(true);
  };

  /** 分组增删改之后：重拉分组列表，选中指定的那个（删组或未指定时回落「全部关注」）。 */
  const onGroupsChanged = async (preferId?: number) => {
    await reloadGroups();
    setActiveGroupId(preferId);
    actionRef.current?.reload(true);
  };

  const onChangeGroup = async (watchId: number, groupId: number | null) => {
    try {
      await updateWatch(watchId, { group_id: groupId });
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责。**catch 不能省**：Select 的
      // onChange 不消费返回的 promise，少了它就是一条 unhandled rejection——浏览器里
      // 进控制台，vitest 里让整个进程退出码非 0。
      return;
    }
    await reloadGroups();
    actionRef.current?.reload();
  };

  const onRemove = async (watchId: number) => {
    let res;
    try {
      res = await removeWatches([watchId]);
    } catch {
      // 同上：Popconfirm 的 onConfirm 同样不消费返回的 promise。
      return;
    }
    message.success('已取消关注');
    setWatchCount(res.watch_count);
    await reloadGroups();
    actionRef.current?.reload();
  };

  const onImported = async () => {
    await reloadGroups();
    actionRef.current?.reload(true);
  };

  const quotaPercent = maxWatch > 0 ? Math.round((watchCount / maxWatch) * 100) : 0;

  return (
    <PageContainer title="竞对监控">
      <Card size="small" styles={{ body: { padding: 12 } }}>
        <Space align="center" size="large">
          <Typography.Text>
            已关注 <Typography.Text strong>{watchCount}</Typography.Text> / {maxWatch || '—'}
          </Typography.Text>
          {maxWatch > 0 && (
            <Progress
              percent={quotaPercent}
              size="small"
              style={{ width: 160 }}
              status={quotaPercent >= 100 ? 'exception' : 'normal'}
              showInfo={false}
            />
          )}
          <Button type="primary" onClick={() => setImportOpen(true)}>
            添加卖家
          </Button>
        </Space>
      </Card>

      <div style={{ marginTop: 12 }}>
        <GroupBar
          groups={groups}
          activeId={activeGroupId}
          totalWatchCount={watchCount}
          onSelect={switchGroup}
          onChanged={onGroupsChanged}
          onCreate={createGroup}
          onRename={renameGroup}
          onDelete={(group) => deleteGroup(group.group_id)}
        />
      </div>

      <ProTable<WatchRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="watch_id"
        columns={buildWatchColumns(sites, groups, onChangeGroup, onRemove)}
        search={false}
        options={false}
        dateFormatter="string"
        pagination={{ pageSize: 50, showSizeChanger: true }}
        locale={{
          emptyText:
            activeGroupId === undefined
              ? '还没有关注任何卖家。点击上方「添加卖家」粘贴文本或导入书签'
              : '这个分组还没有卖家',
        }}
        request={async (params) => {
          const res = await listWatches({
            group_id: activeGroupId ?? null,
            page: params.current,
            perPage: params.pageSize,
          });
          setWatchCount(res.watch_count);
          setMaxWatch(res.max_watch);
          return {
            data: res.data ?? [],
            total: res.total ?? 0,
            success: res.success !== false,
          };
        }}
      />

      <ImportModal
        open={importOpen}
        groups={groups}
        sites={sites}
        onClose={() => setImportOpen(false)}
        onImported={onImported}
      />
    </PageContainer>
  );
}
