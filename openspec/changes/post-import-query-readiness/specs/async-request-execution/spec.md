## ADDED Requirements

### Requirement: 交互查询有执行时间预算

面向请求的异步读路径 SHALL 设置数据库侧的语句执行超时（默认 30 秒，可配置）。超时 SHALL 使数据库停止执行该语句并释放资源，MUST NOT 只限制等待连接的时间。导入、导出与迁移使用的同步引擎 MUST NOT 受此预算约束。执行超时 SHALL 返回 503 统一错误体，`code` 为 `db.query_timeout`，`message` 提示缩小筛选范围后重试，且 MUST NOT 泄漏 SQL。

理由：没有执行上限时，一条计划不佳的查询会无限期占用连接，异步连接池耗尽后其他页面一并失去响应。

#### Scenario: 超时的分析查询被数据库终止

- **WHEN** 一次分析查询的某条语句执行超过交互预算
- **THEN** 数据库取消该语句，接口返回 503 且 `code` 为 `db.query_timeout`，该连接回到池中可被复用

#### Scenario: 导入与导出不受交互预算约束

- **WHEN** 一次导入或导出的单条语句执行超过交互预算
- **THEN** 该语句继续执行，不因交互预算被取消

### Requirement: 分析查询并发有上限且排队有界

`POST /api/analysis/search` SHALL 在每个工作进程内限制同时执行的数量（默认 4，可配置），使异步连接池始终留有连接给其他请求。等待执行的请求 SHALL 有等待上限（默认 5 秒，可配置），超过上限 SHALL 返回 503 统一错误体，`code` 为 `analysis.busy`，MUST NOT 无限排队。

#### Scenario: 分析查询占满时轻量请求仍可响应

- **WHEN** 同一工作进程内已有达到上限数量的分析查询在执行
- **THEN** 作用域、鉴权等其他请求仍能获取连接并正常返回

#### Scenario: 排队超时返回繁忙

- **WHEN** 一次分析查询等待执行超过等待上限仍未轮到
- **THEN** 返回 503 且 `code` 为 `analysis.busy`，该请求不再占用排队位置
