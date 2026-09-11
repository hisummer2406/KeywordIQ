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

