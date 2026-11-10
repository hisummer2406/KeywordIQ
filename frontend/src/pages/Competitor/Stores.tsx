import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { history, useSearchParams } from '@umijs/max';
import { Button, Card, Progress, Radio, Space, Typography, message } from 'antd';
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
import SellerAsinsDrawer from './SellerAsinsDrawer';
import StoreTab from './StoreTab';
import { buildWatchColumns } from './columns';

/** 店铺类型写在 URL（`?type=`）里：刷新、收藏、分享都停在同一类，不回落到默认。 */
type StoreType = 'amazon' | 'shopify';

const TYPE_OPTIONS: { label: string; value: StoreType }[] = [
  { label: 'Amazon 卖家', value: 'amazon' },
  { label: 'Shopify 店铺', value: 'shopify' },
];

/**
 * 竞对监控 · 店铺管理（#166 D2）：管理关注对象并检查数据更新状态。
 *
 * **一页内按店铺类型切换，不按来源拆菜单**：Amazon 与 Shopify 是来源类型，不是任务；把它们
 * 和「商品发现」并列成同级菜单，正是 #166 要消掉的层级混杂。两类对象的字段几乎不重叠
 * （Shopify 有上架时间、变体与币种；Amazon 只有我们首次见到的时刻），所以仍是两张表、不合表。
 *
 * **`GroupBar` 在类型切换之外**，因为分组是两类共用的同一张 `user_seller_group`——分组是纯
 * 标签，不该因成员类型分裂成两套体系。它摆在表格上方而不是侧栏：左侧已经有任务菜单。
 *
 * **进屏即查**：这一屏的空表格恒与"还没做选择"无关——行集合就是用户自己的关注清单。
 */
export default function CompetitorStoresPage() {
  const actionRef = useRef<ActionType>();
  const [searchParams, setSearchParams] = useSearchParams();
  const storeType: StoreType = searchParams.get('type') === 'shopify' ? 'shopify' : 'amazon';
  const [asinsOf, setAsinsOf] = useState<WatchRow | null>(null);

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

  const sellerTab = (
    <>
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

      <ProTable<WatchRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="watch_id"
        columns={buildWatchColumns(sites, groups, onChangeGroup, onRemove, setAsinsOf, (r) =>
          history.push(
            `/competitor/discovery?seller=${encodeURIComponent(r.seller_id)}&site=${encodeURIComponent(r.site)}`,
          ),
        )}
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

      <SellerAsinsDrawer
        watch={asinsOf}
        domain={asinsOf ? sites.find((s) => s.code === asinsOf.site)?.domain : undefined}
        onClose={() => setAsinsOf(null)}
      />
    </>
  );

  return (
    <PageContainer title="店铺管理">
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

      <Radio.Group
        optionType="button"
        buttonStyle="solid"
        style={{ marginTop: 12, marginBottom: 12 }}
        value={storeType}
        options={TYPE_OPTIONS}
        onChange={(e) => setSearchParams(e.target.value === 'shopify' ? { type: 'shopify' } : {})}
      />

      {storeType === 'amazon' ? (
        sellerTab
      ) : (
        <StoreTab groups={groups} activeGroupId={activeGroupId} onGroupsChanged={reloadGroups} />
      )}
    </PageContainer>
  );
}
