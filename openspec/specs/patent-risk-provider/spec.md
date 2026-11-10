# patent-risk-provider Specification

## Purpose
智慧芽 Data REST API 的接入边界：Key 只从服务端 .env 读取、固定源站客户端、调用留痕与错误分类（失败、未知、未发出分开）（#24）。

## Requirements
### Requirement: 智慧芽 API Key 只从服务端环境配置读取

API Key SHALL 只来自服务端环境变量 `ZHIHUIYA_API_KEY`（`.env`），系统 MUST NOT 提供在页面上录入、回显或存库 Key 的入口。Key MUST NOT 出现在任何 API 响应、日志、数据库、前端构建产物或浏览器存储中。

`ZHIHUIYA_API_KEY` 为空时，系统 SHALL 视为「未配置」：`/api/risk/meta` 返回 `provider_configured=false`，外观专利页提示联系管理员配置，提交被拒绝（422）且 MUST NOT 发起任何外部调用；已排队的任务在认领时 SHALL 失败为 `not_configured`。去掉 Key MUST NOT 影响任何历史核查证据的读取。

#### Scenario: 未配置 Key

- **WHEN** `ZHIHUIYA_API_KEY` 为空，用户打开外观专利页
- **THEN** 页面显示「智慧芽接口未配置，请联系管理员」，提交按钮不可用，外部调用次数为 0

#### Scenario: meta 不泄露 Key

- **WHEN** 已配置 Key，用户请求 `/api/risk/meta`
- **THEN** 响应只含 `provider_configured=true`，不含 Key 的任何片段

### Requirement: 供应商客户端固定源站并按契约调用四个接口

客户端 SHALL 只请求 `https://connect.zhihuiya.com`，MUST NOT 接受任何来自用户或配置的源站地址。鉴权 SHALL 使用 `Authorization: Bearer <Key>` 请求头，MUST NOT 使用 `apikey` 查询参数。客户端 SHALL 按以下契约调用：

- P010 `POST /image-search/image-upload`，multipart 字段 `image`；
- P060 `POST /search/patent/image-single`，JSON 请求体，`patent_type=D`、`model=1`、`is_https=1`、`url`，以及用户确认的 `country`、`limit`、`offset`；
- P011 `GET /basic-patent-data/simple-bibliography` 与 P013 `GET /basic-patent-data/legal-status`，`patent_id` 逗号分隔，每次最多 100 个。

外部调用 MUST NOT 在事件循环中阻塞，也 MUST NOT 在等待期间持有数据库事务或连接。请求日志、HTTP 客户端日志和异常日志 MUST NOT 记录 `Authorization` 请求头或 Key。

#### Scenario: 模拟服务核对请求形状

- **WHEN** 测试用模拟 HTTP 服务接收真实客户端发出的四类请求
- **THEN** 能看到 Bearer 请求头、multipart 字段 `image`、P060 的 `patent_type=D` 与 `model=1`、P011/P013 的逗号分隔 `patent_id`

### Requirement: 每次供应商调用都留痕，错误分类且计费标记为未知

每一次 HTTP 调用 SHALL 写一条调用记录：接口编号、所属任务、开始和结束时间、结果（`success`、`error`、`unknown`）、HTTP 状态、供应商错误码、错误分类、计费状态。系统没有可靠的计费回执，计费状态 SHALL 固定为「未知」，MUST NOT 伪造余额、单价或剩余额度。

超时、连接中断、上游 5xx、非 JSON 或 HTML 响应、`status=true` 但结构不符合契约（例如来源总数大于 0 而列表为空，或候选缺少 `patent_id`）SHALL 记为 `unknown`。供应商明确返回的错误码 SHALL 记为 `error` 并归入以下分类之一：`credential_invalid`、`no_permission`、`insufficient_balance`、`client_expired`、`quota_exceeded`、`rate_limited`、`bad_request`、`upstream_error`、`unexpected_code`。

供应商错误 MUST NOT 映射为应用的 401，MUST NOT 把供应商 `error_msg`、上传 URL、内部路径或 Key 透传给前端；前端只拿到分类和对应的中文提示。

#### Scenario: 上游返回 HTML

- **WHEN** P060 返回 HTTP 502 和一段 HTML
- **THEN** 调用记为 `unknown`，任务状态为 `unknown`，界面显示「结果未知，可能已计费」，不显示为「无命中」

#### Scenario: 余额不足

- **WHEN** P060 返回 `error_code=67200005`
- **THEN** 调用记为 `error / insufficient_balance`，界面提示余额或次数不足，用户仍保持登录

#### Scenario: 成功但结构异常

- **WHEN** P060 返回 `status=true`、`total_search_result_count=30`、`patent_messages=[]`
- **THEN** 调用记为 `unknown`（结构异常），该页不计入「已读取」，不记为无命中

