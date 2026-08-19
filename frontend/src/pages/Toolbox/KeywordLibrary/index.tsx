import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType } from '@ant-design/pro-components';
import {
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { formatSiteLabel } from '@/pages/Analysis/siteLabel';
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
import { SELECTION_COLUMN_WIDTH, buildWatchColumns, watchTableWidth } from './columns';
import { toSortParams } from './sort';

/**
 * 我的词库：把关心的词攒成清单，之后打开就看这批词今天排到第几、掉没掉。
 *
 * **进屏即查**（与持续增长市场屏相反）。判据是空表格的含义：增长榜的空表有三种成因
 * （没查 / 窗口缺期 / 口径下无命中），必须先让用户做选择；而盯盘的行集合就是用户自己的
 * 清单，空表只有一种含义——组里没有词。
 *
 * **没有站点选择器**（左侧那个是分组列表的过滤器，不是查询作用域）。站点由分组决定，
 * 给盯盘一个站点选择器就会出现「选了 DE 却在看 US 分组」这种自相矛盾的状态。
 *
 * **入库入口只在分析查询屏**：从结果行加入时 keyword_id 是现成的，词必然真实存在于该
 * 站点；手工录入则要按文本反查，错别字、多余空格、不属于该站点的词都要各自定义失败
 * 行为，而这些在一个盯盘清单里没有正确答案。所以这一屏只能移除、不能新增。
 */
export default function KeywordLibraryPage() {
  const actionRef = useRef<ActionType>();

  const [sites, setSites] = useState<SiteOption[]>([]);
  const [siteFilter, setSiteFilter] = useState<string | undefined>();
  const [groups, setGroups] = useState<KeywordGroup[]>([]);
  const [activeId, setActiveId] = useState<number | undefined>();

  const [view, setView] = useState<View>('DAY');
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState<string | undefined>();
  /** 该站点一期数据都没有。空表格必须说清是"没有数据"而不是"清单是空的"。 */
  const [scopeHasNoDates, setScopeHasNoDates] = useState(false);

  const [selected, setSelected] = useState<number[]>([]);
  const [naming, setNaming] = useState<{ open: boolean; target?: KeywordGroup; value: string }>({
    open: false,
    value: '',
  });

  const active = groups.find((g) => g.group_id === activeId);
  const siteDomain = sites.find((s) => s.code === active?.site)?.domain;

  /** 拉分组列表，并在需要时选中一个（首屏与增删之后都走这里）。 */
  const reloadGroups = async (site?: string, prefer?: number) => {
    const list = await listGroups(site);
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

  const submitName = async () => {
    const name = naming.value.trim();
    if (!name) return;
    if (naming.target) {
      await renameGroup(naming.target.group_id, name);
      await reloadGroups(siteFilter, naming.target.group_id);
    } else {
      const site = siteFilter ?? sites[0]?.code;
      if (!site) return;
      const created = await createGroup(site, name);
      await reloadGroups(siteFilter, created.group_id);
      await reloadScope(created.group_id, view);
      actionRef.current?.reload(true);
    }
    setNaming({ open: false, value: '' });
  };

  const onDeleteGroup = async (group: KeywordGroup) => {
    await deleteGroup(group.group_id);
    message.success(`已删除分组「${group.name}」`);
    const next = await reloadGroups(siteFilter);
    if (next) await reloadScope(next.group_id, view);
    actionRef.current?.reload(true);
  };

  const onRemoveItems = async () => {
    if (!activeId || !selected.length) return;
    const result = await removeItems(activeId, selected);
    message.success(`已从词库移除 ${result.removed} 个词`);
    setSelected([]);
    await reloadGroups(siteFilter, activeId);
    actionRef.current?.reload(true);
  };

  /**
   * 空表格的**三种**含义必须分开说，否则用户会去排查一个不存在的问题。
   *
   * 「一个分组都没有」曾被并进「这个分组还没有词」，那句话在没有分组时是错的——它把
   * 用户引向「去加词」，而他真正缺的是先建一个组。
   */
  const emptyText = !active
    ? '还没有分组。先在左边新建一个分组，再到「ABA数据选品」把关心的词加进来'
    : scopeHasNoDates
      ? '这个站点还没有导入任何数据，暂时无法盯盘'
      : '这个分组还没有词。到「ABA数据选品」勾选结果行，点「加入词库」即可加入';

  return (
    <PageContainer title="我的词库">
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <Card
          size="small"
          // 280 是实测出来的：自己排完之后，「站点标签 + 分组名 + 条数 + 两个按钮」
          // 在这个宽度下一行放得下且分组名还有约 100px 可读（见下面 renderItem 的注释）。
          style={{ width: 280, flex: '0 0 280px' }}
          title="分组"
          extra={
            <Button
              type="link"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => setNaming({ open: true, value: '' })}
              disabled={!sites.length}
            >
              新建
            </Button>
          }
        >
          <Select
            style={{ width: '100%', marginBottom: 8 }}
            allowClear
            placeholder="全部站点"
            value={siteFilter}
            onChange={(site) => {
              setSiteFilter(site);
              reloadGroups(site)
                .then((next) => (next ? reloadScope(next.group_id, view) : undefined))
                .then(() => actionRef.current?.reload(true))
                .catch(() => undefined);
            }}
            options={sites.map((site) => ({ label: formatSiteLabel(site), value: site.code }))}
          />
          <List
            size="small"
            dataSource={groups}
            locale={{ emptyText: <Empty description="还没有分组" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            renderItem={(group) => (
              <List.Item
                onClick={() => switchGroup(group.group_id)}
                style={{
                  cursor: 'pointer',
                  background: group.group_id === activeId ? '#e6f4ff' : undefined,
                }}
              >
                {/*
                  **不用 List.Item 的 `actions`**：antd 给那个 <ul> 挂了固定的
                  `margin-inline-start: 48px`，在 260px 的侧栏里直接吃掉近四分之一宽度
                  ——实测内容区只剩 122px，「(6)」被挤成 9px 宽逐字竖排。这 48px 从
                  组件外面改不掉，索性自己排。

                  两个 `minWidth: 0` 也不可省：flex 子项默认 min-width 是 auto，放不下
                  时不收缩而是把文字压成竖排。配 ellipsis 才是「放不下就截断」。
                */}
                <div
                  style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}
                >
                  <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>{group.site}</Tag>
                  <Typography.Text ellipsis style={{ flex: 1, minWidth: 0 }} title={group.name}>
                    {group.name}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ flexShrink: 0 }}>
                    ({group.item_count})
                  </Typography.Text>
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    title="重命名"
                    onClick={(e) => {
                      e.stopPropagation();
                      setNaming({ open: true, target: group, value: group.name });
                    }}
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
                    onConfirm={() => onDeleteGroup(group)}
                  >
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      title="删除分组"
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Popconfirm>
                </div>
              </List.Item>
            )}
          />
        </Card>

        <div style={{ flex: 1, minWidth: 0 }}>
          <Card size="small" styles={{ body: { padding: 12 } }}>
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
                  站点 {active.site}（由分组决定）· 共 {active.item_count} 个词
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
            // 必须给 scroll.x：不给的话 AntD 用 table-layout: auto，列上的 width 只是建议值。
            // 这里要把状态列与复选框列一并算进去，漏掉的表现是最右一列被挤扁。
            scroll={{ x: watchTableWidth(view) }}
            pagination={{ pageSize: 50, showSizeChanger: true }}
            rowSelection={{
              // 与分析屏同因：不给选择列宽度，rc-table 就不用 fixed 布局，
              // 所有列宽退回建议值（见 columns.ts 的 watchTableWidth）。
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
        </div>
      </div>

      <Modal
        open={naming.open}
        title={naming.target ? '重命名分组' : '新建分组'}
        okText="保存"
        cancelText="取消"
        onOk={submitName}
        onCancel={() => setNaming({ open: false, value: '' })}
      >
        {!naming.target && (
          <Typography.Paragraph type="secondary">
            分组绑定站点：{siteFilter ?? sites[0]?.code}。<strong>站点建后不可修改</strong>
            ，换站点请另建分组。
          </Typography.Paragraph>
        )}
        <Input
          value={naming.value}
          maxLength={50}
          placeholder="分组名，如「主推词」"
          onChange={(e) => setNaming((n) => ({ ...n, value: e.target.value }))}
          onPressEnter={submitName}
        />
      </Modal>
    </PageContainer>
  );
}
