## MODIFIED Requirements

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
