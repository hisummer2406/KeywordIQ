## MODIFIED Requirements

### Requirement: 机会中心权限点默认仅超管可见

系统 SHALL 提供可授予的权限点 `opportunity:view`，作为「机会中心」菜单与其全部端点的唯一权限判据。自 change `add-opportunity-dossier` 起（2026-11-12 用户决定推进到运营可用），该权限点 SHALL 授予 admin、operator、analyst 三个内置角色；`is_super` 用户照旧无条件可见。未持有该权限点的自定义角色 MUST 看不到菜单、访问不了端点。

前端菜单显隐 SHALL 由该权限点驱动；菜单隐藏 MUST NOT 被当作访问控制，后端每个机会中心端点 SHALL 各自挂 `opportunity:view` 权限依赖。

#### Scenario: 运营可见

- **WHEN** `is_super=false`、角色为 operator 的用户登录
- **THEN** 顶栏出现「机会中心」，`/opportunity` 可正常打开

#### Scenario: 无权限的自定义角色不可见

- **WHEN** 一个角色不持有 `opportunity:view` 的非超管用户登录
- **THEN** 顶栏不出现「机会中心」；直接访问 `/opportunity` 被路由守卫挡回，请求机会中心端点得到 403

#### Scenario: 内置角色授权矩阵

- **WHEN** 迁移应用到已有数据库
- **THEN** `role_permission` 中 admin、operator、analyst 三个角色各有一行 `opportunity:view`

## ADDED Requirements

### Requirement: 机会档案是机会中心的默认入口

机会中心页内左栏 SHALL 以「机会档案」为第一项，未指定视图时 SHALL 显示档案列表。档案列表、新建档案与档案详情 SHALL 与专家、定时任务、任务对话同在 `/opportunity` 单页内，视图状态写在 URL 查询参数里，刷新可恢复；MUST NOT 新增二级路由。

#### Scenario: 打开机会中心

- **WHEN** 具备 `opportunity:view` 的用户点击「机会中心」
- **THEN** 主区显示机会档案列表，地址仍是 `/opportunity`

#### Scenario: 刷新档案详情

- **WHEN** 用户打开某个档案后刷新页面
- **THEN** 仍显示同一档案

### Requirement: 计费的专利检索不随机会中心权限铺开

机会中心授予内置角色后，IP 专家里会向智慧芽计费的动作——执行检索、取证、设置定时复检——SHALL 在 `opportunity:view` 之外另外要求 `risk:search`。`risk:search` 按既有约定只默认授予 admin（检索按次计费，不随角色默认铺开）。不持有 `risk:search` 的用户仍 SHALL 能创建调研、生成检索方案、追问与签署人工意见。前端 SHALL 在发起任务处说明这一限制；访问控制 MUST 由后端端点执行，不依赖前端提示。

#### Scenario: 运营发起计费检索

- **WHEN** 角色为 operator、不持有 `risk:search` 的用户对自己的调研提交「执行检索」
- **THEN** 返回 403，不入队任何任务，不产生 MCP 调用

#### Scenario: 运营创建调研

- **WHEN** 同一用户创建一条专利调研并把它关联到机会档案
- **THEN** 创建与关联成功
