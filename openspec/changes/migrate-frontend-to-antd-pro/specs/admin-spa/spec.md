## ADDED Requirements

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

> **口径收窄记录（2026-08-01，unify-api-error-contract）**：本条原先允许失败以
> `success: false` 混在 HTTP 200 里返回。实践中这让前端无法把「查询失败」与「没有数据」
> 区分开——分析屏的作用域错误只表现为一个没有解释的空表格。失败路径已统一到异常，
> `protable_error` 已移除。归档本变更时请勿把旧口径 sync 回主 specs。

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

#### Scenario: 请求自动携带令牌

- **WHEN** 前端发起任一受保护的 API 请求
- **THEN** 请求头自动带上 `Authorization: Bearer <token>`

#### Scenario: 令牌失效跳转登录

- **WHEN** 某受保护请求返回 401
- **THEN** 前端清除本地令牌并跳转到登录界面

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
