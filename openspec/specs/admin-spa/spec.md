# admin-spa Specification

## Purpose

后台管理界面以独立的 React SPA（Ant Design Pro / Umi Max，TypeScript）交付，取代原先由
`fastapi-amis-admin` 服务端生成 JSON schema 渲染的 amis 页面。后端退回到「只提供 JSON API +
托管 SPA 静态产物」，不再承担 UI 渲染职责。本能力覆盖：SPA 的交付与路由回退方式、列表类
API 的统一响应契约（ProTable 约定）、前端侧的 JWT 鉴权与失效处理、OSS 直传在新前端的原生
实现，以及导入归属（站点/周期/报告日期）完全由文件名判定、不提供界面绕过途径的约束。
## Requirements
### Requirement: 后台以独立 React SPA 交付

后台界面 SHALL 由一个独立的 React 单页应用（Ant Design Pro / Umi）提供，后端 MUST NOT
再服务端渲染后台页面，只 SHALL 提供 JSON API 并托管 SPA 静态产物。SPA 的前端路由 SHALL 在
未匹配到静态资源时回退到应用入口（HTML5 history 模式），使深链接可直接访问。

#### Scenario: SPA 承载后台界面

- **WHEN** 用户访问后台任一界面（分析查询 / 用户管理 / 上传）
- **THEN** 由 React SPA 渲染，后端仅通过 JSON API 提供数据，不返回服务端渲染的页面

#### Scenario: 前端路由深链接可用

- **WHEN** 用户直接访问某个前端路由的深链接且该路径无对应静态资源
- **THEN** 后端回退返回 SPA 入口 HTML，由前端路由解析并渲染目标界面

### Requirement: 列表接口遵循统一的表格响应契约

供表格消费的列表类 API SHALL 在**成功**时返回统一契约 `{ data, total, success: true }`
（Ant Design `ProTable` 约定），MUST NOT 保留 amis 专用的响应形状。该契约 SHALL 由统一响应
适配层产生，避免逐接口散改。

失败 SHALL 走 `api-error-contract` 定义的非 2xx 统一错误契约，MUST NOT 以 HTTP 200 携带
`success: false` 表达。

#### Scenario: 分页列表返回 ProTable 契约

- **WHEN** 前端表格请求某分页列表接口且请求合法
- **THEN** 响应体为 `{ data: [...], total: <总数>, success: true }`，前端表格据此渲染与分页

#### Scenario: 列表失败走非 2xx，不混在 200 里

- **WHEN** 某列表接口处理失败
- **THEN** 响应状态码为非 2xx，响应体为统一错误契约
  （`{success:false, code, message, request_id}`），前端由唯一出口统一提示
- **AND** MUST NOT 返回 HTTP 200 + `success: false`

### Requirement: 前端统一鉴权与失效处理

SPA SHALL 复用现有 JWT：在请求拦截器中统一注入 `Authorization: Bearer <token>`，并在收到
未授权响应（401）时清除本地凭证并跳转登录。后端鉴权语义 SHALL 保持不变（令牌来源与校验不因
前端更替而改变）。

401 的处理 SHALL 按错误码区分原因：账号会话被顶替（`auth.session_invalid`）SHALL 呈现
"账号已在其他浏览器登录"这一明确原因；其余 401（令牌过期、格式无效等）沿用既有的"登录已
过期，请重新登录"提示。两者都触发清除本地凭证并跳转登录，区别只在提示文案。

处理任一 401 响应前，前端 SHALL 校验该响应对应的失败请求，发起时所使用的令牌是否仍是
当前本地持有的令牌；若两者不一致（说明本浏览器在该请求发出之后已经通过更新的登录取得了
新令牌，这是一个迟到的、属于旧令牌的响应），MUST NOT 清除本地凭证或跳转登录——这类响应
SHALL 被静默忽略，不清理已经生效的新登录状态。

#### Scenario: 请求自动携带令牌

- **WHEN** 前端发起任一受保护的 API 请求
- **THEN** 请求头自动带上 `Authorization: Bearer <token>`

#### Scenario: 令牌失效跳转登录

- **WHEN** 某受保护请求返回 401，且该请求发起时使用的令牌与当前本地持有的令牌一致
- **THEN** 前端清除本地令牌并跳转到登录界面

#### Scenario: 会话被顶替呈现明确原因

- **WHEN** 某受保护请求返回 401 且错误码为 `auth.session_invalid`，请求发起时使用的令牌
  与当前本地持有的令牌一致
- **THEN** 前端提示"账号已在其他浏览器登录"，清除本地凭证（含分析屏会话缓存）并跳转登录

#### Scenario: 迟到的旧请求响应不误踢新登录

- **WHEN** 某受保护请求在发出之后、响应到达之前，本浏览器已经通过重新登录取得了一个新令牌
  并已保存到本地，随后该请求的响应以 401 到达
- **THEN** 前端识别出该响应对应的令牌已不是当前本地持有的令牌，MUST NOT 清除本地凭证、
  MUST NOT 跳转登录、MUST NOT 弹出提示

### Requirement: OSS 直传由新前端原生实现

上传界面 SHALL 用 Ant Design `Upload` 的自定义上传（`customRequest`）+ 浏览器 OSS SDK 实现
STS 临时凭证的分片直传，文件字节 MUST NOT 经过应用服务器；上传完成后前端 SHALL 回传 object key
触发导入。后端 `/api/upload/oss/*` 契约 SHALL 保持不变（换前端不改后端契约）。amis 时期为直传
临时注入的 JS SHALL 被移除。

#### Scenario: 新前端直传并触发导入

- **WHEN** 用户在新前端选择一个日报或周报 CSV 上传
- **THEN** 前端向后端换取受限 STS 凭证、用 OSS SDK 分片直传到 OSS、再回传 object key 触发导入，字节不经应用服务器

#### Scenario: 直传进度可见

- **WHEN** 大文件（如约 1.5GB 周报）正在直传
- **THEN** 前端展示分片上传进度，完成后进入导入处理状态轮询

### Requirement: 上传归属完全由文件名判定

一次导入的归属是「站点 + 周期 + 报告日期」三元组，它决定数据写入哪张事实表的哪个分区。
该三元组 SHALL **完全由文件名判定**：上传界面 SHALL NOT 提供站点或周期的选择项，用户唯一
的输入就是那个文件。

判定结果 SHALL 在上传开始之前完整回显（三项都显示），使「这份文件会落到哪一期」在文件
开始上传前就是可见的。

任一项无法从文件名判定时，系统 SHALL 拒绝本次上传并提示缺失项与文件名应有的形态，且
MUST NOT 用默认值继续、也 MUST NOT 提供在界面上绕过文件名的途径——绕过途径正是让界面
所示与实际写入的归属产生分歧的来源。**周期尤其 MUST NOT 采用「非日报即周报」式的兜底**，
它会把关键词缺失的日报静默写进周报事实表，界面上无从察觉。

后端接口 SHALL 继续接受显式的站点/周期参数（本地导入退路与脚本化调用需要），但当显式值
与文件名判定结果冲突时 SHALL 拒绝，MUST NOT 任选一方静默继续。

#### Scenario: 文件名可完整判定时零输入

- **WHEN** 用户拖入 `DE_热门搜索词_简单_Day_2026_07_16.csv`
- **THEN** 界面回显站点 DE、周期 日报、报告日期 2026-07-16，无需任何选择，文件直接开始上传

#### Scenario: 周期无法判定时拒绝并提示

- **WHEN** 用户拖入的文件名既不含日报关键词也不含周报关键词
- **THEN** 系统拒绝该文件并提示补全文件名中的周期标识，不默认按周报处理，不发起上传

#### Scenario: 站点无法判定时拒绝并提示

- **WHEN** 文件名不带已登记站点的前缀（未带前缀，或前缀不是已登记站点码）
- **THEN** 系统拒绝该文件并提示补全站点前缀，不发起上传

#### Scenario: 接口层显式指定与文件名冲突时拒绝

- **WHEN** 调用方向接口显式传入站点 US，而文件名前缀是 DE
- **THEN** 接口拒绝并指出冲突，不写入任何数据

### Requirement: 前端组件库版本基线

SPA SHALL 以 antd 6 作为组件库主版本，`@ant-design/icons` 的主版本 SHALL 与 antd 保持一致（同为 6）。
项目代码 MUST NOT 混装 antd 5 和 antd 6，也 MUST NOT 混装 icons 5 和 icons 6。

如果某个依赖的 peer 声明不覆盖当前的 antd 主版本（例如 `@ant-design/pro-components` 2.x
只声明了 antd 4 和 5），这个越界 SHALL 在包管理器配置里显式放行，并写明原因和验证依据。
MUST NOT 靠忽略安装告警或关闭严格 peer 检查来让它通过。

前端生产构建 SHALL 通过 esbuild helper 冲突检查。出现冲突时，SHALL 通过调整构建产物的形态
来消除冲突，MUST NOT 以关闭检查的方式放行。如果产物形态的调整会让构建流程内置的检查不再执行，
`build` 脚本 SHALL 显式对产物运行独立的冲突检查，使 CI 和部署脚本调用 `pnpm build` 时仍然强制执行。

#### Scenario: 依赖主版本一致

- **WHEN** 检查前端的锁定依赖
- **THEN** 只有一份 antd 6.x 被项目代码解析到，`@ant-design/icons` 的主版本为 6

#### Scenario: peer 越界被显式记录

- **WHEN** 某个依赖的 peer 声明不包含 antd 6，但项目经过验证后仍然使用它
- **THEN** 包管理器配置里有一条针对这个依赖与 antd 的放行规则，旁边注明原因和验证依据；
  不存在全局放宽 peer 检查的配置

#### Scenario: 生产构建通过产物检查

- **WHEN** 运行前端生产构建（`pnpm build`）
- **THEN** 构建成功，并且对产物目录的 esbuild helper 冲突检查已经执行、输出无冲突；该检查不依赖构建流程内置的钩子，
  检查发现冲突时 `pnpm build` 以非零状态退出

### Requirement: 项目代码不使用组件库的废弃 API

项目自身的前端代码（`frontend/src`）MUST NOT 使用当前 antd 主版本标记为废弃的组件、属性或取值，
SHALL 改用对应的替代写法。依赖库内部触发的废弃告警不受本要求约束，但 SHALL 在升级依赖时复查。

整个组件被废弃、而替代组件的接口并不等价的情形不在本要求范围内（当前只有 `List`，替代品 `Listy` 是虚拟列表接口，不是等价替换）。
这类使用处 SHALL 登记在对应变更的 design 里，写明数量、位置和不迁移的理由，并在后续变更中迁移。

#### Scenario: 开发环境无项目代码触发的废弃告警

- **WHEN** 运行前端测试，或在开发环境打开各主要页面（市场分析、词库、竞对、导入、用户管理、定价、机会中心）并完成常规操作
- **THEN** 测试输出和浏览器控制台里没有由项目代码触发的 `[antd: …] … is deprecated` 告警，已登记的例外除外

#### Scenario: 新代码沿用替代写法

- **WHEN** 新增或修改前端代码，需要用到已有替代写法的废弃能力（例如 Space 的方向、Alert 的标题、弹层关闭后销毁）
- **THEN** 使用替代写法（`orientation`、`title`、`destroyOnHidden` 等），不使用废弃写法

### Requirement: 对话类界面的组件与安全边界

SPA 的对话类界面（机会中心的专家任务对话、机会档案的追问）SHALL 使用与 antd 主版本匹配的 Ant Design X 作为气泡、输入框与会话列表组件，
`@ant-design/x` 的主版本 SHALL 满足 antd 6 的 peer 要求。

对话输入框的提交动作 MUST 只触发「向大模型追问」。任何会调用付费外部服务（如专利检索、取证）的动作 MUST NOT 由对话输入框的提交或回车直接触发，
SHALL 继续经过显示调用次数的确认后才入队。

大模型与外部供应商返回的文本 MUST 作为纯文本渲染，MUST NOT 经由 Markdown 或 HTML 渲染器输出。
如需渲染 Markdown，SHALL 另行变更并单独评估注入面，MUST NOT 随组件库接入顺带引入。

#### Scenario: 回车只发送追问

- **WHEN** 用户在专家任务对话的输入框里输入问题并按 Enter
- **THEN** 只创建一次追问任务，不产生检索或取证任务，也不消耗检索预算

#### Scenario: 输入法组合态下回车不提交

- **WHEN** 用户用中文输入法在输入框里选词，按 Enter 确认候选
- **THEN** 不提交，也不丢失已输入内容

#### Scenario: 大模型返回含标记的文本

- **WHEN** 大模型回答里包含 `<script>`、`<img onerror=…>` 或 Markdown 链接语法
- **THEN** 页面按字面文本显示，不产生 DOM 元素，也不渲染成链接或图片

#### Scenario: 对话输入忙时不可提交

- **WHEN** 任务有在途的排队或执行中的工作
- **THEN** 追问输入框不可提交，且界面说明原因（等当前任务完成），不发出请求

