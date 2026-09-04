## ADDED Requirements

### Requirement: 机会中心权限点默认仅超管可见

系统 SHALL 提供可授予的权限点 `opportunity:view`，作为「机会中心」菜单与其未来端点的
唯一权限判据。该权限点 MUST NOT 被授予给任何内置角色（admin、operator、analyst）——
默认状态下只有 `is_super` 用户能看到这个菜单，普通角色即便是 `admin` 角色码也看不到，
除非该用户的 `is_super` 为真。

前端菜单显隐 SHALL 由该权限点驱动；菜单隐藏 MUST NOT 被当作访问控制,后端未来新增的
每个机会中心端点 SHALL 各自挂 `opportunity:view` 权限依赖。

#### Scenario: 超管可见

- **WHEN** `is_super=true` 的用户登录，且该用户所在角色不持有 `opportunity:view`
- **THEN** 顶栏出现「机会中心」，`/opportunity` 可正常打开

#### Scenario: 普通角色不可见

- **WHEN** 一个 `is_super=false`、角色为 admin/operator/analyst 之一的用户登录
- **THEN** 顶栏不出现「机会中心」；直接访问 `/opportunity` 被路由守卫挡回

#### Scenario: 内置角色授权矩阵

- **WHEN** 迁移应用到已有数据库
- **THEN** `permission` 表新增一行 `opportunity:view`，但 `role_permission` 表中
  不存在任何 `opportunity:view` 对应的行——admin/operator/analyst 都不持有它

### Requirement: 机会中心是承接主动式工作流的单页顶层菜单

界面 SHALL 提供「机会中心」一级菜单（`/opportunity`），作为确定性 Signal、候选机会、
验证推进与风险核查这条工作流的业务入口。该菜单 MUST NOT 以「AI Agent」「聊天助手」
「工具箱」这类命名——前者把技术实现误当成用户任务，后者是按排除法命名的泛化容器。

该菜单 SHALL 保持无子项的单页顶层形态（与「关键词市场分析」「竞对监控」同构）：
内部的多个状态（待处理信号、候选机会、验证中、风险待核查、已通过、已否决/已归档）
SHALL 用页内 Tab 或筛选承载，MUST NOT 拆成独立路径的二级路由或左侧栏。

#### Scenario: 打开机会中心

- **WHEN** 具备 `opportunity:view` 的用户点击「机会中心」
- **THEN** 页面显示六个状态的 Tab（待处理信号/候选机会/验证中/风险待核查/已通过/
  已否决），没有左侧二级菜单

### Requirement: 能力未完成阶段不得伪造数据或发起不存在的请求

机会中心的信号计算、机会评分、验证流程等业务能力均由本变更之外的其他变更承接
（issue #7、#13、#24、#25、#38、#54、#55）。在这些能力接入之前，机会中心的每个状态
Tab SHALL 显示明确的阶段说明（例如"信号计算尚未接入"），MUST NOT 显示编造的 0 值、
机会分或风险结论,也 MUST NOT 让空状态显得像是"查询后没有命中"。

页面 MUST NOT 向任何不存在的后端端点发起请求。系统 MUST NOT 为了让页面"看起来能用"
而新增一个总是返回空结果的占位端点——那等于伪造出一个可以被查询失败的入口，与
「数据源尚未接入」这件事本身不符。

#### Scenario: 打开任意状态 Tab

- **WHEN** 用户切换到「候选机会」Tab
- **THEN** 页面显示"该能力尚未接入"一类的说明文案，网络面板中没有该屏发出的请求

#### Scenario: 提议加一个返回空数组的占位端点

- **WHEN** 有人提出为机会中心新增一个 `GET /api/opportunity/*` 端点、在业务逻辑接入前
  先返回空列表
- **THEN** 该提议被拒绝，理由是没有数据源支撑的端点只会把"没做"伪装成"查了但没有"
