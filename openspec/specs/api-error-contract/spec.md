# api-error-contract Specification

## Purpose
失败的统一表达方式：所有失败响应只有一种形状（success/code/message/request_id），业务错误一律以
异常抛出而非返值，状态码按语义分级（含基础设施故障与业务 5xx 分开），响应体不泄漏内部细节、
校验错误不回显请求内容，request id 贯穿请求头/日志/错误体。另含三条边界：流式端点的失败必须在
开流前判定、后台任务自捕获且带栈、前端错误提示有唯一出口且静默必须显式声明。
## Requirements
### Requirement: 失败响应遵循单一契约

所有失败响应 SHALL 使用同一形状 `{success: false, code, message, request_id}`，
且 SHALL 以非 2xx 的 HTTP 状态码返回。业务失败 MUST NOT 以 HTTP 200 返回。

`code` SHALL 形如 `<域>.<原因>` 的语义化字符串（如 `user.duplicate_name`）。
`message` SHALL 是可直接展示给用户的中文。

HTTP 状态码 SHALL 按语义映射：400 参数不合法、401 未认证、403 无权限、404 不存在、
409 与当前状态冲突、413 超出规模上限、422 语义合法但业务不接受、500 未预期错误、
503 服务暂时不可用且稍后重试有意义（数据库不可用、查询执行超时、查询繁忙）。

#### Scenario: 业务失败返回非 2xx 与统一错误体

- **WHEN** 客户端以已存在的用户名调用 `POST /api/user/create`
- **THEN** 响应状态码为 409，响应体为 `{success: false, code: "user.duplicate_name", message: "用户名已存在", request_id: <id>}`
- **AND** 响应体 MUST NOT 含 `status` 或 `msg` 字段

#### Scenario: 作用域非法返回 422

- **WHEN** 客户端以未登记的站点码调用 `POST /api/analysis/search`
- **THEN** 响应状态码为 422，`code` 标识作用域错误，且 HTTP 200 的空结果 MUST NOT 被用来表达该失败

#### Scenario: 导出超出行数上限返回 413

- **WHEN** 导出结果超过 `EXPORT_ROW_CAP`
- **THEN** 响应状态码为 413，`code` 为导出超限，`message` 提示补充筛选条件
- **AND** 响应 MUST NOT 是一份被截断的 CSV

#### Scenario: 可重试的基础设施失败返回 503

- **WHEN** 交互查询执行超时或因并发上限排队超时
- **THEN** 响应状态码为 503，`code` 分别为 `db.query_timeout` 与 `analysis.busy`，`message` 为可展示的中文且 MUST NOT 含 SQL

### Requirement: 业务错误以异常表达

服务层 SHALL 通过抛出 `APIError` 及其子类表达业务失败，API 层 MUST NOT 手工拼装错误响应体。
失败路径 MUST NOT 依赖调用方主动检查返回值——未被检查的失败会被静默忽略，这正是本变更要消除的
缺陷类别。

未被显式处理的异常 SHALL 由全局异常处理器兜底为 500 统一错误体，MUST NOT 泄漏到 FastAPI 的
默认 `{detail}` 形状。

#### Scenario: 未捕获异常被兜底为统一错误体

- **WHEN** 某个端点的处理过程中抛出未预期的异常（如数据库连接失败）
- **THEN** 响应状态码为 500，响应体符合统一错误契约，`code` 为 `internal.unhandled`
- **AND** 完整异常栈进入日志，MUST NOT 出现在响应体中

#### Scenario: 服务层异常不需要 API 层逐一转译

- **WHEN** 服务层抛出 `APIError` 子类
- **THEN** 对应的 HTTP 状态码与错误体由全局处理器产生，API 层无需 `try/except` 也能得到正确响应

### Requirement: 每个请求可追踪

系统 SHALL 为每个请求生成或沿用一个 request id，写入响应头 `X-Request-Id` 与失败响应体的
`request_id` 字段，并使其可被日志记录读取。

入站请求已携带 `X-Request-Id` 时 SHALL 沿用该值而非重新生成，以便跨层串联。

#### Scenario: 失败响应携带可对回日志的 id

- **WHEN** 任一请求失败
- **THEN** 响应体的 `request_id` 与响应头 `X-Request-Id` 一致
- **AND** 该 id 出现在服务端对应这次请求的日志记录中

#### Scenario: 沿用上游注入的 id

- **WHEN** 入站请求带有 `X-Request-Id: abc123`
- **THEN** 响应头与错误体中的 id 均为 `abc123`

### Requirement: 流式端点的失败在开流前判定

产出流式响应的端点 SHALL 在返回 `StreamingResponse` 之前完成全部可预见的校验
（作用域、权限、规模上限）。一旦开始产出响应体，状态码与响应头已发出，此后的失败无法再表达为
错误响应，只会得到一个内容不完整的 200 响应。

生成器内部发生的意外异常 SHALL 被记录日志（含 request id），客户端表现为传输中断。

#### Scenario: 可预见的失败在开流前返回错误

- **WHEN** 导出请求的作用域非法或结果超出上限
- **THEN** 服务端返回非 2xx 的统一错误体，MUST NOT 已经开始产出 CSV 内容

#### Scenario: 开流后的异常被记录且不伪装成成功

- **WHEN** CSV 产出过程中发生未预期异常
- **THEN** 异常连同 request id 进入日志，响应流中断
- **AND** 服务端 MUST NOT 以正常结束的方式收尾，使客户端误认为文件完整

### Requirement: 列表接口的成功契约与失败路径分离

供表格消费的列表类 API SHALL 在**成功**时返回 `{data, total, success: true}`（Ant Design
ProTable 约定）。失败 SHALL 走统一错误契约的非 2xx 路径，MUST NOT 以 HTTP 200 携带
`success: false` 表达。

#### Scenario: 列表成功返回 ProTable 契约

- **WHEN** 客户端请求分页列表且请求合法
- **THEN** 响应为 `{data: [...], total: <总数>, success: true}`

#### Scenario: 列表失败不混在 200 里

- **WHEN** 列表请求因作用域非法或服务端错误而失败
- **THEN** 响应状态码为非 2xx 且响应体为统一错误契约
- **AND** MUST NOT 返回 HTTP 200 + `success: false`

### Requirement: 响应体不泄漏内部细节

失败响应的 `message` SHALL 只包含面向用户的说明。异常栈、SQL 语句、文件系统路径、
OSS object key、连接串等内部细节 MUST NOT 出现在响应体中。

`internal.unhandled` 的 `message` SHALL 是固定的通用文案，MUST NOT 透传原始异常字符串。

应用 MUST NOT 以 `debug=True` 构造 FastAPI 实例——该模式下框架会把完整 traceback 写入
500 响应体。

#### Scenario: 兜底错误不透传异常文本

- **WHEN** 服务端抛出携带敏感信息的未预期异常
- **THEN** 响应 `message` 为通用文案，原始异常文本只存在于日志中

#### Scenario: 500 响应体不含 traceback 与 SQL

- **WHEN** 任一端点抛出未捕获异常，其异常消息中含 SQL 语句或文件路径
- **THEN** 响应体为统一错误契约的 JSON，MUST NOT 含 traceback、SQL 语句或文件路径

### Requirement: 校验错误不回显请求内容

参数校验失败的响应 SHALL 只包含出错字段的位置与原因说明。**MUST NOT 回显客户端提交的原始值**
——Pydantic 的错误对象在字段缺失时会把整个请求体作为 `input` 带出，直接透传等于把用户提交的
凭证、密码等内容写回响应体与访问日志。

响应中 MUST NOT 包含 Pydantic 的 `input` 字段与文档 `url` 字段。

#### Scenario: 缺失必填字段时不回显请求体

- **WHEN** 客户端向登录端点提交缺少 `username`、但含 `password` 的请求体
- **THEN** 响应状态码为 422，指出 `username` 字段缺失
- **AND** 响应体 MUST NOT 包含所提交的 `password` 值

#### Scenario: 校验错误形状与其他失败一致

- **WHEN** 参数校验失败
- **THEN** 响应体为统一错误契约（`success` / `code` / `message` / `request_id`），
  字段说明置于独立的 `fields` 列表
- **AND** `detail` MUST NOT 既在某些响应里是字符串、又在另一些响应里是数组

### Requirement: 失败按类别分级记录且 5xx 可告警

服务端 SHALL 对失败分级记录：客户端错误（4xx）以 WARNING 级记录且不触发告警；
服务端错误（5xx）以 ERROR 级记录并携带完整异常栈，且 SHALL 可被接入告警渠道。

未捕获异常 MUST NOT 只存在于 uvicorn 的标准错误输出——它 SHALL 进入应用日志文件，
否则运维在 `logs/app.log` 中看不到任何痕迹。

#### Scenario: 4xx 不产生告警噪音

- **WHEN** 客户端提交非法作用域导致 422
- **THEN** 日志以 WARNING 级记录该失败及其 request id，不触发告警

#### Scenario: 5xx 带栈进入应用日志

- **WHEN** 端点抛出未捕获异常
- **THEN** 应用日志以 ERROR 级记录完整异常栈与 request id

### Requirement: 基础设施故障与业务失败分开表达

数据库连接失败、超时、连接池耗尽等基础设施故障 SHALL 与业务失败区分：它们 SHALL 映射为
独立的错误码与 503，MUST NOT 与业务侧的 500 混为一类，否则日志级别与告警策略无法分别设定。

#### Scenario: 数据库不可用返回 503 而非 500

- **WHEN** 请求处理过程中数据库连接超时或连接池耗尽
- **THEN** 响应状态码为 503，`code` 标识基础设施不可用，`message` 提示稍后重试
- **AND** 日志中该失败可与业务侧 500 区分

### Requirement: 后台任务的失败有独立处理路径

全局异常处理器不拦截 `BackgroundTasks` 中抛出的异常。后台任务 SHALL 自行捕获全部异常，
并 SHALL 以携带完整异常栈的方式记录（`logger.exception` 或等价方式）——只记录异常的字符串
表示会丢失栈，而 GB 级导入失败时栈往往是唯一线索。

后台任务 MUST NOT 让异常逃逸到调用方。

#### Scenario: 后台导入失败保留异常栈

- **WHEN** 后台导入任务中抛出未预期异常
- **THEN** 应用日志记录完整异常栈与该批次的标识信息
- **AND** 异常不逃逸出后台任务

### Requirement: 前端错误提示有唯一出口且静默必须显式

前端 SHALL 以请求层的统一错误处理器作为错误提示的唯一出口。页面层 MUST NOT 依赖逐处
`try/catch` 才能让用户看见失败。

确需不提示的场景 SHALL 通过请求层的显式选项声明，MUST NOT 通过页面层捕获后不作为来实现——
静默必须是显式声明的结果，而非遗漏处理的副产品。

#### Scenario: 未写 try/catch 的页面仍能呈现失败

- **WHEN** 用户在管理界面提交一个会被服务端拒绝的操作，且该页面未写 `try/catch`
- **THEN** 用户看到来自统一出口的错误提示，内容为服务端返回的 `message`
- **AND** 界面 MUST NOT 停留在没有任何反馈的状态

#### Scenario: 静默场景经显式声明

- **WHEN** 登录页提交了错误的凭证，且该请求已显式声明跳过统一提示
- **THEN** 不弹出全局提示，由表单自行呈现错误

