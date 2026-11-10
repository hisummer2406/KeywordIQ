## MODIFIED Requirements

### Requirement: 单页顶层菜单不得因分组而长出侧栏

布局是 `layout: 'mix'` + `splitMenus`，顶层节点有子项即长出左侧栏、无子项则内容区全宽。

关键词市场分析与机会中心 SHALL 保持**无子项的单页顶层**形态。系统 MUST NOT
为了分组整齐而给它们添加二级菜单——分析查询屏是全产品最宽的表（指标列、走势小图、
三个历史排名列），一条约 208px 的侧栏是每次查询都在付的永久损失，而横向被裁掉的列
找不回来。机会中心内部的多个状态（待处理信号/候选机会/验证中等）SHALL 用同一路由内
的 Tab 或筛选承载，MUST NOT 拆成带独立路径的二级路由。

这条与「我的词库」「竞对监控」两屏拒绝把分组区做成侧栏是同一笔账。

竞对监控不在这份名单内，它按任务拆成子菜单（见「竞对监控以任务菜单组织，不按来源分菜单」，#166）。理由是两张主表都远窄于分析查询屏，侧栏的代价可以接受；这个例外 MUST NOT 被引用为给分析查询屏或机会中心加子菜单的先例。

#### Scenario: 打开分析查询屏

- **WHEN** 用户进入 `/analysis`
- **THEN** 页面没有左侧二级菜单，表格占据整个内容区宽度

#### Scenario: 打开机会中心

- **WHEN** 用户进入 `/opportunity`
- **THEN** 页面没有左侧二级菜单，状态切换通过页内 Tab 完成，URL 不随 Tab 切换改变

#### Scenario: 提议把关键词工具并进选品

- **WHEN** 有人提出按数据域把增长榜与词库并进 关键词市场分析 之下
- **THEN** 该提议被拒绝，理由是它会让分析查询屏长出侧栏

## ADDED Requirements

### Requirement: 竞对监控以任务菜单组织，不按来源分菜单

竞对监控 SHALL 保留为一级菜单，下设且只设两个子菜单：「商品发现」（`/competitor/discovery`，默认）与「店铺管理」（`/competitor/stores`）。子菜单按运营任务划分，Amazon 与 Shopify 是来源类型，SHALL 以页内的来源筛选或店铺类型切换呈现，MUST NOT 成为与任务并列的菜单项。

左侧菜单 SHALL 可折叠。分组 SHALL 仍是页面内的筛选与管理内容，MUST NOT 再叠加一条固定的分组侧栏。

旧入口 `/competitor` SHALL 重定向到商品发现，MUST NOT 落空。整个模块 SHALL 只要求 `competitor:view`，MUST NOT 为子菜单新增逐页权限点。逐页授权没有真实的业务需求，只会多出「勾了也没有区分意义」的复选框。

#### Scenario: 打开竞对监控

- **WHEN** 拥有 `competitor:view` 的用户点击顶栏「竞对监控」，或直接访问 `/competitor`
- **THEN** 进入 `/competitor/discovery`，左侧菜单只有「商品发现」「店铺管理」两项，且可以折叠

#### Scenario: 无竞对权限

- **WHEN** 只有 `analysis:view` 的用户访问 `/competitor/stores`
- **THEN** 看不到竞对监控菜单，直接访问时被现有的无权限页拦下

#### Scenario: 提议按来源加菜单

- **WHEN** 有人提出给「Amazon 卖家」「Shopify 店铺」各加一个菜单项
- **THEN** 该提议被拒绝，理由是来源属于筛选维度，不是任务；与任务并列会重现 #166 所说的层级混杂
