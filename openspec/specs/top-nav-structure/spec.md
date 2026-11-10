# top-nav-structure Specification

## Purpose
TBD - created by archiving change restructure-top-nav. Update Purpose after archive.
## Requirements
### Requirement: 顶栏一级菜单按数据域命名，不设按排除法命名的容器

顶栏 SHALL 由六个一级菜单构成，顺序固定为：机会中心、关键词市场分析、关键词工具、
竞对监控、风险核查、数据管理。

每个一级菜单的名字 SHALL 说明它装的是什么（数据域或使用场景），MUST NOT 是
「工具箱」「其他」这类按排除法命名的容器——这种名字不给新功能提供归属判据，
下一个功能只能问「它像不像已经在里面的那些」，而那个问题没有答案。

排序判据 SHALL 是：机会中心是主动式工作流的入口排最前；看数据的三项（关键词市场
分析、关键词工具、竞对监控）相邻；风险核查次之；喂数据/管理数据的「数据管理」排最后。

#### Scenario: 顶栏渲染

- **WHEN** 具备全部权限（含 `is_super`）的用户登录
- **THEN** 顶栏依次显示 机会中心、关键词市场分析、关键词工具、竞对监控、风险核查、数据管理

#### Scenario: 新增功能的归属判断

- **WHEN** 要为一个新功能选一级菜单
- **THEN** 判据是它读哪个数据域；没有任何一项匹配时新增一级菜单，
  MUST NOT 塞进一个泛化容器

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

### Requirement: 关键词工具菜单容器

界面 SHALL 提供「关键词工具」一级菜单（`/keyword`），下挂持续增长市场
（`/keyword/growth`）与我的词库（`/keyword/library`）两个二级项。

这两屏 SHALL 是该菜单的全部内容——它们的共同点是主题（都是 ABA 关键词的透镜），
不是「都不属于主线」。访问 `/keyword` SHALL 重定向到持续增长市场。

#### Scenario: 展开关键词工具

- **WHEN** 具备 `keyword:view` 的用户点击「关键词工具」
- **THEN** 左侧显示持续增长市场与我的词库两项

#### Scenario: 访问父路由

- **WHEN** 用户直接访问 `/keyword`
- **THEN** 重定向到 `/keyword/growth`

### Requirement: 关键词工具权限点

系统 SHALL 提供可授予的权限点 `keyword:view`，作为持续增长市场与我的词库两屏的唯一
权限判据，并授予 admin / operator / analyst 三个内置角色。

该权限点 SHALL 是**容器级共用点**。系统 MUST NOT 为两屏各自新增 `library:*`、`growth:*`
这类逐屏权限点——我的词库管理的是用户自己的私有清单，「禁止某人管理自己的收藏夹」
不是会真实发生的授权需求，那样的权限点只会全员常开。

前端菜单显隐 SHALL 由该权限点驱动；菜单隐藏 MUST NOT 被当作访问控制，后端端点 SHALL
各自挂权限依赖。

#### Scenario: 无权限用户

- **WHEN** 不具备 `keyword:view` 的用户登录
- **THEN** 顶栏不出现「关键词工具」；直接访问 `/keyword/growth` 被路由守卫挡回；
  直接请求榜单端点返回 403

#### Scenario: 内置角色

- **WHEN** 迁移应用到已有数据库
- **THEN** admin / operator / analyst 三个角色都持有 `keyword:view`，无需人工补授，
  且系统中不再存在 `toolbox:view`

#### Scenario: 不存在逐屏权限点

- **WHEN** 不具备 `keyword:view` 的用户直接请求任一词库端点
- **THEN** 返回 403，且系统中不存在名为 `library:*` 或 `growth:*` 的权限点

### Requirement: 未接入数据源的占位屏

外观专利与全球商标库 SHALL 各自渲染一个明确说明「数据源尚未接入」的占位屏。占位屏
MUST NOT 发起任何后端请求，系统 MUST NOT 为它们新增端点。

把它们放进顶栏是自觉的取舍：产品最空的一块因此成为最显眼的一块，换取的是「这块在做」
这个路线图信号可见。占位屏自己 SHALL 把状态说清楚，MUST NOT 让人误以为有数据。

#### Scenario: 打开占位屏

- **WHEN** 用户点击「外观专利」
- **THEN** 页面显示空状态与「数据源尚未接入」的说明，网络面板中没有该屏发出的请求

### Requirement: 旧工具箱地址不落空

`/toolbox`、`/toolbox/growth`、`/toolbox/library` 三条旧地址 SHALL 重定向到对应新地址，
且不出现在菜单里。

两个占位屏的旧地址 MUST NOT 保留重定向——没有人会去书签一个写着「数据源尚未接入」
的屏，逐条保留只会让路由表越来越长。

#### Scenario: 打开旧书签

- **WHEN** 用户访问 `/toolbox/library`
- **THEN** 重定向到 `/keyword/library`，页面正常加载

#### Scenario: 占位屏旧地址

- **WHEN** 用户访问 `/toolbox/patent`
- **THEN** 落到 404 屏，不做重定向

### Requirement: 风险核查菜单容器不设权限点

界面 SHALL 提供「风险核查」一级菜单（`/ip`），下挂外观专利（`/ip/patent`）与
全球商标库（`/ip/trademark`）两个二级项。访问 `/ip` SHALL 重定向到外观专利。

该菜单 MUST NOT 拥有权限点，访问判据 SHALL 只是「已登录」。两屏都没有后端端点，
没有端点可挂的权限点是「勾了也不生效」的复选框，与已被移除的 `user:manage` 同类。
接入数据源、有了真实端点之后再新增权限点——那时是加法，不会留下失效的历史勾选。
「仅超管可见」不是这类占位屏缺的东西，缺的是数据源本身，给它挂一个可配置的权限点
不能让数据变得存在。

#### Scenario: 任意已登录用户

- **WHEN** 一个只有 `analysis:view` 的用户登录
- **THEN** 顶栏出现「风险核查」，两个二级屏均可打开

#### Scenario: 权限矩阵

- **WHEN** 查看系统的可授予权限点清单
- **THEN** 其中不存在 `ip:*` 或任何只作用于这两屏的权限点

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
