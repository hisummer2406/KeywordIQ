import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import { Button, Card, Popconfirm, Radio, Select, Space, Typography, message } from 'antd';
import { useEffect, useRef, useState } from 'react';
import type { View } from '@/services/analysis';
import {
  createGroup,
  deleteGroup,
  getLibraryScope,
  listGroups,
  removeItems,
  renameGroup,
  watchGroup,
  type KeywordGroup,
  type LibraryWatchRow,
} from '@/services/library';
import { getSites, type SiteOption } from '@/services/upload';
import GroupBar from './GroupBar';
import { SELECTION_COLUMN_WIDTH, buildWatchColumns, watchTableWidth } from './columns';
import { toSortParams } from './sort';

/**
 * 我的词库：把关心的词攒成清单，之后打开就看这批词今天排到第几、掉没掉。
 *
 * **分组管理是表格上方独立的一块，不是侧栏。** 这一屏的横向是稀缺资源：表格要 1688px，
 * 1920 视口下整屏也只有 1584px 可用；侧栏形态曾把可视区压到 1292px，近 400px 被裁进横向
 * 滚动（实测）。改成横铺的一块之后，分组一览仍在（一眼看到有哪些清单、各有多少词），
 * 代价换成了纵向的几行——纵向可以滚，横向被裁掉的列是找不回来的。
 *
 * **进屏即查**（与持续增长市场屏相反）。判据是空表格的含义：增长榜的空表有三种成因
 * （没查 / 窗口缺期 / 口径下无命中），必须先让用户做选择；而盯盘的空表都与"还没做选择"
 * 无关——行集合就是用户自己的清单。
 *
 * **没有站点选择器**：站点由分组决定。给盯盘一个站点选择器就会出现「选了 DE 却在看
 * US 分组」这种自相矛盾的状态。
 *
 * **入库入口只在分析查询屏**：从结果行加入时 keyword_id 是现成的，词必然真实存在于该
 * 站点；手工录入则要按文本反查，错别字、多余空格、不属于该站点的词都要各自定义失败
 * 行为，而这些在一个盯盘清单里没有正确答案。所以这一屏只能移除、不能新增。
 */
export default function KeywordLibraryPage() {
  const actionRef = useRef<ActionType>();

  const [sites, setSites] = useState<SiteOption[]>([]);
  const [groups, setGroups] = useState<KeywordGroup[]>([]);
  const [activeId, setActiveId] = useState<number | undefined>();

  const [view, setView] = useState<View>('DAY');
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState<string | undefined>();
  /** 该站点一期数据都没有。空表格必须说清是"没有数据"而不是"清单是空的"。 */
  const [scopeHasNoDates, setScopeHasNoDates] = useState(false);

  const [selected, setSelected] = useState<number[]>([]);

  const active = groups.find((g) => g.group_id === activeId);
  const siteDomain = sites.find((s) => s.code === active?.site)?.domain;

  /** 拉分组列表，并在需要时选中一个（首屏与增删之后都走这里）。 */
  const reloadGroups = async (prefer?: number) => {
    const list = await listGroups();
    setGroups(list);
    const next = list.find((g) => g.group_id === prefer) ?? list[0];
    setActiveId(next?.group_id);
    return next;
  };

  /** 拉某个分组的可选期次。站点取自分组，故只需要 group_id 与视图。 */
  const reloadScope = async (groupId: number, nextView: View) => {
    const scope = await getLibraryScope(groupId, nextView);
    setDates(scope.dates);
    setScopeHasNoDates(scope.dates.length === 0);
    setDate(scope.dates[0]);
    return scope;
  };

  useEffect(() => {
    getSites()
      .then(setSites)
      .catch(() => undefined);
    (async () => {
      const first = await reloadGroups();
      if (first) await reloadScope(first.group_id, 'DAY');
      // 表格自己会在挂载时调一次 request；那时 activeId 已经就位。
      actionRef.current?.reload(true);
    })().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchGroup = async (groupId: number) => {
    setActiveId(groupId);
    setSelected([]);
    await reloadScope(groupId, view).catch(() => undefined);
    actionRef.current?.reload(true);
  };

  const switchView = async (nextView: View) => {
    setView(nextView);
    if (!activeId) return;
    // 期次清单按视图取，换视图必须重拉，否则用户会在一份属于另一个视图的日期清单里选。
    await reloadScope(activeId, nextView).catch(() => undefined);
    actionRef.current?.reload(true);
  };

  /** 分组增删改之后：重拉分组，选中指定的那个（删掉当前分组时回落到第一个）。 */
  const onGroupsChanged = async (preferId?: number) => {
    const next = await reloadGroups(preferId ?? activeId);
    if (next) await reloadScope(next.group_id, view);
    setSelected([]);
    actionRef.current?.reload(true);
  };

  const onRemoveItems = async () => {
    if (!activeId || !selected.length) return;
    const result = await removeItems(activeId, selected);
    message.success(`已从词库移除 ${result.removed} 个词`);
    setSelected([]);
    await reloadGroups(activeId);
    actionRef.current?.reload(true);
  };

  /**
   * 空表格的**三种**含义必须分开说，否则用户会去排查一个不存在的问题。
   *
   * 「一个分组都没有」曾被并进「这个分组还没有词」，那句话在没有分组时是错的——它把
   * 用户引向「去加词」，而他真正缺的是先建一个组。
   */
  const emptyText = !active
    ? '还没有分组。先在上面新建一个分组，再到「关键词市场分析」把关心的词加进来'
    : scopeHasNoDates
      ? '这个站点还没有导入任何数据，暂时无法盯盘'
      : '这个分组还没有词。到「关键词市场分析」勾选结果行，点「加入词库」即可加入';

  return (
    <PageContainer title="我的词库">
      <GroupBar
        groups={groups}
        sites={sites}
        activeId={activeId}
        onSelect={switchGroup}
        onChanged={onGroupsChanged}
        onCreate={createGroup}
        onRename={renameGroup}
        onDelete={(group) => deleteGroup(group.group_id)}
      />

      <Card size="small" style={{ marginTop: 12 }} styles={{ body: { padding: 12 } }}>
        <Space wrap>
          <Radio.Group
            value={view}
            onChange={(e) => switchView(e.target.value as View)}
            optionType="button"
            buttonStyle="solid"
            options={[
              { label: '按天', value: 'DAY' },
              { label: '按周', value: 'WEEK' },
            ]}
          />
          <Select
            style={{ width: 160 }}
            value={date}
            placeholder="无可用期次"
            onChange={(next) => {
              setDate(next);
              actionRef.current?.reload(true);
            }}
            options={dates.map((d) => ({ label: d, value: d }))}
            notFoundContent="该站点暂无数据"
          />
          {active && (
            <Typography.Text type="secondary">
              {active.name} · 站点 {active.site}（由分组决定）· 共 {active.item_count} 个词
            </Typography.Text>
          )}
          {selected.length > 0 && (
            <Popconfirm
              title={`从「${active?.name}」移除 ${selected.length} 个词`}
              okText="移除"
              cancelText="取消"
              onConfirm={onRemoveItems}
            >
              <Button danger>移出词库（{selected.length}）</Button>
            </Popconfirm>
          )}
        </Space>
      </Card>

      <ProTable<LibraryWatchRow>
        style={{ marginTop: 12 }}
        actionRef={actionRef}
        rowKey="keyword_id"
        columns={buildWatchColumns(view, siteDomain)}
        search={false}
        options={false}
        dateFormatter="string"
        // 与分析屏同因，见 #43：不显式给 tableLayout，列宽只是建议值。
        tableLayout="fixed"
        scroll={{ x: watchTableWidth(view) }}
        pagination={{ pageSize: 50, showSizeChanger: true }}
        rowSelection={{
          // 与分析屏同因：不给选择列宽度，colgroup 里就是空值，而 scroll.x 已按 48 算过它。
          columnWidth: SELECTION_COLUMN_WIDTH,
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys as number[]),
        }}
        locale={{ emptyText }}
        request={async (params, sort) => {
          if (!activeId) return { data: [], total: 0, success: true };
          const res = await watchGroup({
            group_id: activeId,
            view,
            date,
            page: params.current,
            perPage: params.pageSize,
            ...toSortParams(sort),
          });
          return {
            data: res.data ?? [],
            total: res.total ?? 0,
            success: res.success !== false,
          };
        }}
      />
    </PageContainer>
  );
}
