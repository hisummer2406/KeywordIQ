# 智慧芽专利数据库 MCP 精简契约

2026-11-11 用真实 Key 实测（PatSnap Patent MCP Server 4.0.4）。官方文档（open.zhihuiya.com/devportal/guides/mcp-*）
只写了传输与鉴权，没写计费、QPS、会话与检索语法；下面以实测为准。样例是脱敏后的形状示意。

## 传输

- 端点固定 `https://connect.zhihuiya.com/1458a4/mcp`，Streamable HTTP，`Authorization: Bearer sk-...`（与 REST 同一把 Key）。
- 请求头 `Accept: application/json, text/event-stream`。响应为 `text/event-stream`，正文是一条 `data: {jsonrpc...}`。
- `initialize` 不返回 `mcp-session-id`：服务无会话。客户端每个任务初始化一次，再连续 `tools/call`。
- 不用 `?apikey=` 查询参数（会进 URL 日志）。

## 结果形状

`result.structuredContent`（`result.content[0].text` 是同一份 JSON）：

```json
{"success": true, "data": ..., "error_code": 0, "error_msg": "", "timestamp": "..."}
{"success": false, "error_code": 68300004, "error_msg": "Parameter query invalid: query syntax error"}
{"success": true, "data": {}, "error_code": 0, "error_msg": ""}
```

最后一行是检索式合法但零命中时的实际返回：`data` 是空对象，不是空列表。客户端把它规范成
`{result_count: 0, total_search_result_count: 0, results: []}`，记为「本次范围内未找到」。

## 本期白名单工具

| 工具 | 参数 | 成功时的 `data` |
|---|---|---|
| `search_patents` | `query_text`（必填）、`limit`、`offset` | `{result_count, total_search_result_count, results[]}`；每条 `patent_id`、`pn`、`apno`、`title`、`original_assignee`、`current_assignee`、`inventor`、`apdt`、`pbdt`、`authority` |
| `get_patent_legal_status` | `patent_id` 或 `patent_number` | `[{patent_id, pn, simple_legal_status[]}]` |
| `bibliography` | 同上 | `[{patent_id, pn, bibliographic_data{patent_type, publication_reference, application_reference, classification_data{classification_loc[]}, parties?, ...}}]` |
| `abstract_image` | 同上 | `[{patent_id, pn, abstract_drawing{num, path}}]`；`path` 带签名，约 1 小时失效 |

其余 16 个工具（同族、引用、权利要求、说明书、翻译、法律事件等）本期不开放。

## 检索式语法（实测）

| 写法 | 结果 |
|---|---|
| `TTL:(cup)` | 可用，标题 |
| `TA:(mug OR cup)` | 可用，标题 + 摘要；括号内可用 OR |
| `LOC:(07-01)` | 可用，只命中外观设计 |
| `AUTHORITY:(US)`、`AUTHORITY:(US OR EU OR WO)` | 可用 |
| `APD:[20200101 TO 20231231]` | 可用，申请日范围 |
| `PT:(D)`、未知字段 | `success=false, error_code=68300004`（语法错误） |

## 错误分类

| 情况 | outcome / 分类 |
|---|---|
| `success=false` 带错误码 | error，按 `app/table/risk/provider_client.py` 的错误码表（68300004 → `bad_request`） |
| JSON-RPC `error`、`isError=true` 无错误码 | error / `upstream_error` |
| HTTP 401 / 403 / 429 | error / `credential_invalid` / `no_permission` / `rate_limited` |
| 连接失败、本地构造客户端失败 | error / `connect_failed` / `client_error`（请求未到达供应商） |
| 超时、断流、HTTP 5xx、非 JSON、结构不符（含总数大于 offset 而列表为空） | unknown |

## 未验证

- 计费口径（按次还是按条）、QPS、是否对失败调用计费。
- 欧盟外观设计在 MCP 检索结果中的 `authority` 值（P060 返回 `EU`，MCP 组合检索前 10 条未出现 EU）。
