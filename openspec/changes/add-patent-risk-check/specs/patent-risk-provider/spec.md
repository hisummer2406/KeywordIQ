## ADDED Requirements

### Requirement: 智慧芽 API Key 只由超管在服务端配置，加密保存且不回显

系统 SHALL 提供智慧芽供应商配置，只有 `is_super` 用户可以读取摘要、保存或替换 Key、停用、启用和清除；非超管调用这些端点 SHALL 返回 403。Key SHALL 使用服务端主密钥 `RISK_CREDENTIAL_KEYS` 加密后入库，库中 MUST NOT 出现明文。任何 API 响应、日志、审计记录、前端构建产物和浏览器存储 MUST NOT 包含完整 Key；摘要只显示末 4 位指纹。

主密钥未配置时，保存 SHALL 返回 503 并提示「服务端未配置加密主密钥」，MUST NOT 退回明文存储。密文无法解密时，配置状态 SHALL 显示为「凭证不可读」并阻断调用，MUST NOT 被当作未配置而静默清空。

每次保存、替换、停用、启用、清除和验证都 SHALL 追加一条审计（动作、操作人、时间、指纹、验证结果），审计 MUST NOT 含密钥。清除 Key MUST NOT 删除任何历史核查证据。

#### Scenario: 超管保存 Key

- **WHEN** 超管在配置页输入 `sk-…` 并保存
- **THEN** 库中只有密文与末 4 位指纹，响应和日志中没有完整 Key，输入框被清空，状态显示「已保存，未验证」

#### Scenario: 非超管尝试读写配置

- **WHEN** 拥有 `risk:search` 但 `is_super=false` 的用户请求配置端点
- **THEN** 返回 403，即使其角色勾选了全部权限点

#### Scenario: 主密钥被换掉

- **WHEN** 库中密文无法用当前 `RISK_CREDENTIAL_KEYS` 解密
- **THEN** 配置状态为「凭证不可读」，新的检索任务失败并记为 `credential_unreadable`，不发起任何外部请求

#### Scenario: 清除 Key

- **WHEN** 超管清除 Key
- **THEN** 密文被删除，审计留下一条清除记录，已有核查单的候选、详情和意见全部保留可读

### Requirement: 「已保存」与「已验证」分开，验证必须是明确的付费动作

配置摘要 SHALL 分别显示「是否已保存」「是否启用」「最近一次验证的时间、接口与结果」。保存 Key MUST NOT 自动发起验证调用。验证 SHALL 由超管点击按钮触发，按钮旁 SHALL 说明「将调用一次 P011 简单著录（可能计费）」，验证结果只代表 P011 当次可用，MUST NOT 被表述为四个接口都已开通。

#### Scenario: 只保存不验证

- **WHEN** 超管保存 Key 后没有点验证
- **THEN** 状态为「已保存，未验证」，外部请求次数为 0

#### Scenario: 验证返回无权限

- **WHEN** 验证调用返回错误码 67200004
- **THEN** 配置页显示「该 Key 没有此接口权限」，应用登录态不受影响

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
