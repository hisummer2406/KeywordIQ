## Context

- 机会中心只有导航骨架（#76，`opportunity:view` 默认仅超管）。机会档案、状态机、主理 Agent 都还没有；本变更只落地其中「IP 风险专家」一个角色，挂在「风险待核查」Tab。
- #24（`app/table/risk/`）已有一套成熟做法：持久化任务 + `SKIP LOCKED` + 租约、调用期间不持有数据库连接、每次调用留痕且计费未知、按市场追加的人工意见。本变更沿用这些做法。
- 2026-11-11 对智慧芽 MCP（`https://connect.zhihuiya.com/1458a4/mcp`，PatSnap Patent MCP Server 4.0.4）的实测：
  - Streamable HTTP，响应为 `text/event-stream` 的单条 `data:`；`initialize` 不返回 `mcp-session-id`，服务无会话。
  - `tools/call` 的结果在 `result.structuredContent`：`{success, data, error_code, error_msg}`。语法错误返回 `success=false, error_code=68300004`，不是空结果。
  - `search_patents` 的 `data`：`{result_count, total_search_result_count, results[]}`，每条只有 `patent_id`、`pn`、`apno`、`title`、`original_assignee`、`current_assignee`、`inventor`、`apdt`、`pbdt`、`authority`。
  - 检索式实测可用：`TTL:(...)`、`LOC:(07-01)`（只命中外观设计）、`AUTHORITY:(US)`、`APD:[20200101 TO 20231231]`、`AND`。`PT:(D)` 是语法错误。官方语法页需要登录，未读到。
  - `get_patent_legal_status`、`bibliography`、`abstract_image` 按 `patent_number` 或 `patent_id` 单件查询，`data` 为数组；摘要附图地址带签名，约 1 小时失效。
  - 官方文档没有写计费口径、QPS 与会话要求。
- 项目此前没有接入任何大模型。生产是国内单台 ECS。

## Goals / Non-Goals

**Goals**

- 一条可走通的闭环：填写产品信息 →（可选）Agent 生成检索方案 → 人工确认检索式 → 执行检索 → 选择候选取证 →（可选）Agent 写风险分析 → 按市场签署人工意见。
- 每一次 MCP 与大模型调用都有留痕；MCP 调用受预算约束。
- 大模型 Key 未配置时，人工路径仍完整可用。

**Non-Goals**

- 不做机会档案、主理 Agent、多专家编排或状态机（依赖 #7、#13、#54、#55）。
- 不让大模型自由循环调用工具；不做按图检索（#24 负责）。
- 不改 #24 的 REST 接口与数据，不做租户隔离（现有系统没有租户，见风险）。

## Decisions

### 决策 1：Agent 是受控生成，不是工具循环

大模型只在三个显式动作里各被调用一次：「生成检索方案」「生成风险分析」与对话追问（决策 12）。输出必须是 JSON，经服务端校验后才保存；工具调用（MCP）只由服务端按人工确认的参数执行。

理由：机会中心决策摘要第 3 条「数据库状态机、后台任务和受控工具编排；不引入自由循环的通用 Agent Harness」。两步生成把计费调用的决定权留给人，也让每一步可复核。

### 决策 2：大模型走 OpenAI 兼容协议，配置在 `.env`

`OPPORTUNITY_LLM_BASE_URL`、`OPPORTUNITY_LLM_API_KEY`、`OPPORTUNITY_LLM_MODEL`、`OPPORTUNITY_LLM_TIMEOUT_SECONDS`。请求 `POST {base}/chat/completions`，`response_format={"type":"json_object"}`，`temperature=0.2`。基址只接受 `https://`，客户端 `trust_env=False`、不跟随重定向。三项任一为空即「未配置」。

用户选了国内模型但没定厂商；DeepSeek 与通义都支持这个协议与 JSON 输出。用 httpx 直接实现，不引入 SDK。

### 决策 3：MCP 客户端固定端点、工具白名单

端点写死；只允许四个工具：`search_patents`、`get_patent_legal_status`、`bibliography`、`abstract_image`。每次调用先 `initialize` 再 `tools/call`，同一任务内复用一次初始化。

结果分类：

| 情况 | outcome | 分类 |
|---|---|---|
| `structuredContent.success=true` 且结构符合 | success | — |
| `success=false` 带错误码 | error | 复用 #24 的 `classify_error_code`（如 68300004 → `bad_request`） |
| JSON-RPC `error`、`isError=true` 无错误码 | error | `upstream_error` |
| HTTP 401/403/429 | error | 同 #24 |
| 连接失败、本地构造失败 | error | `connect_failed` / `client_error`（未到达供应商） |
| 超时、断流、5xx、非 JSON、结构不符 | unknown | `timeout` / `network` / `upstream_5xx` / `malformed_response` |

检索结果总数大于 offset 而列表为空，按结构异常记为 unknown，不算无命中。

### 决策 4：预算在入队时用咨询锁核对

一次付费动作入队前算出它会发起的 MCP 调用数（检索：检索式条数；取证：候选数 × 工具数），在 `pg_advisory_xact_lock(0x4B41)` 内核对（沿用仓库 `0x4Bxx` 锁号约定，不与导入 `0x4B49` 等冲突）：

- 本调研：已留痕的 MCP 调用数 + 在途任务的计划数 + 本次 ≤ `OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH`（默认 20）；
- 全站：今天（Asia/Shanghai）已留痕的 MCP 调用数 + 在途任务计划数 + 本次 ≤ `OPPORTUNITY_IP_MCP_DAILY_LIMIT`（默认 200）。

超限返回 422 并带上限、已用与本次所需。失败和未知的调用也计入已用（可能已计费）。执行时只发出计划内的调用，所以不会超出入队时的核对。大模型调用只留痕不设预算：每次都是用户显式点击，且同一调研同时只有一个在途任务。

### 决策 5：持久化任务，与 #24 相同的三段式

`opp_ip_task` 五种 kind：`plan`、`search`、`detail`、`analyze`、`ask`。认领（短事务，`FOR UPDATE SKIP LOCKED`，写租约，抄快照）→ 执行（不持有会话）→ 收尾（新事务写留痕与证据）。同一调研同时至多一个在途任务（部分唯一索引）。租约过期转 unknown，不自动重发。独立轮询，与 #24 同款 `run_poller`。

### 决策 6：证据与附图

- 每条执行过的检索式一行 `opp_ip_query`：检索式、limit、总数、返回数、结果状态与原始 `data`。
- 候选按 `(research_id, patent_id)` 去重，记录首次出现的检索式与名次。
- 详情按 `(candidate_id, tool)` 唯一；已有成功结果的不再调用。
- 摘要附图：在执行阶段下载（只接受 `https://*.zhihuiya.com`，不超过 2 MB，按文件头校验 PNG/JPEG），存进 `opp_ip_detail.image`，经鉴权端点读取。供应商地址约 1 小时失效，不存图会让证据很快不可看。下载失败时详情仍记成功（元数据可用），图片标记缺失。

### 决策 7：Agent 输出的约束

- 检索方案：至多 3 条 `{query_text, rationale}`，检索式 1–500 字符、无控制字符，另有 `assumptions`。服务端不执行，只作为建议填进确认框。
- 风险分析：`summary`、`signals[{candidate_id, relevance(high/medium/low/unclear), reason}]`、`coverage_gaps[]`、`unknowns[]`、`next_steps[]`。不属于本调研的 `candidate_id` 丢弃；任何「可上架」类结论字段都不接受。
- 分析的输入由服务端组装：产品信息、每条检索式的执行状态与总数、候选及已取得的详情。供应商数据放在明确标记的数据段里，系统提示要求把它当作数据而非指令。
- 服务端另外算出确定性的覆盖事实（检索式成功/失败/未知条数、取证覆盖），和分析一起展示；这些不由大模型生成。

### 决策 8：人工意见复用 #24 的规则

请求模型直接复用 `risk_schemas.ReviewCreate`。校验规则抽成 `app/table/risk/review_rules.py::validate_review`，#24 的 `add_review` 改为调用它，避免两份口径：

- 市场必须在调研范围内；引用的候选必须属于本调研；
- 没有任何成功执行的检索式时，不能选「可上架」「条件通过」；
- 条件通过必须有条件、至少一条证据和复核日期。

意见只追加，存 `opp_ip_review`。Agent 分析不写入、不修改意见。

### 决策 9：权限与私有性

全部端点挂 `opportunity:view`（默认仅超管，沿用 opportunity-center 规格），不新增权限点。每条查询都带 `owner_id`，取不到一律 404。

### 决策 10：机会中心改为工作空间 + 对话布局（2026-11-11 用户追加，参考 WorkBuddy）

仍是 `/opportunity` 单页，不新增路由。页内三栏：

- 左栏：「新建任务」按钮；「专家」「定时任务」两个入口；「工作空间」列表（含「未归类」与新建）；选中工作空间下的任务列表（产品名 + 最近任务状态）。
- 主区：任务对话。顶部是任务名、专家、所在工作空间（可移动）、预算；中间是消息流；底部是输入框。
- 右侧抽屉「成果」：候选专利与取证、人工意见（按市场）、任务与调用记录。

视图状态全部写进 URL 查询参数（`view=new|experts|schedules`、`ws=<id>|none`、`task=<id>`），刷新可恢复。有在途任务时每 3 秒轮询详情，没有就停。

消息流由服务端已存的事实拼出，不另存一份：任务简报（调研输入）→ 检索方案卡（可编辑后确认执行）→ 每次检索的结果卡 → 取证结果 → 风险分析卡 → 人工意见签署记录，中间穿插用户追问与 Agent 回答；失败与未知的任务显示为系统消息。所有卡片按时间排序。

新建任务：选专家（目前只有 IP 专家可选）→ 在输入框写产品名称与说明，选销售市场和检索法域（法域推荐仍只作提示，不自动勾选）→ 发送即创建调研；大模型已配置时随即入队「生成检索方案」（只调用大模型，不消耗 MCP 预算）。检索与取证仍必须在卡片里人工确认。

### 决策 11：工作空间

`opp_workspace`（`owner_id`、`name`），按创建人私有；`opp_ip_research.workspace_id` 可空（空即「未归类」），外键 `ON DELETE SET NULL`。任务创建时可指定工作空间，之后可移动——这是组织方式，不属于调研输入，不受「输入不可修改」约束。有任务的工作空间删除时返回 422，避免误删后任务全部散落到「未归类」。同一用户下名称不重复。

### 决策 12：对话追问

`opp_ip_message`（`research_id`、`role`=user/agent、`content`、`task_id`、`model`、`cited_candidate_ids`）。用户发送追问时，在同一事务里写入用户消息与一个 `ask` 任务（与其他任务同样受在途互斥）；执行时把产品信息、覆盖事实、检索与候选证据（与分析同一份输入）、最新分析摘要和最近 10 条对话交给大模型，要求 JSON `{answer, cited_candidate_ids}`。回答截断到 2000 字，引用不属于本调研的候选丢弃；回答存为 agent 消息。系统提示与分析一致：证据段是数据不是指令，不得给出上架结论。追问不调用 MCP，不计 MCP 预算。

### 决策 13：定时复检

`opp_ip_schedule`，每个调研至多一条：频率 `weekly`（周几）或 `monthly`（每月 1–28 日），时刻 0–23 点（Asia/Shanghai），检索式 1–3 条且每条都必须是本调研**已成功执行过**的检索式——这样定时任务执行的仍是人确认过、语法已验证的检索式，没有绕过人工确认。频率只给每周与每月：复检会消耗 MCP 预算，按天跑太快耗尽单调研上限。

到点由 IP 轮询器里的维护性一轮处理：取到期且启用的定时任务（`FOR UPDATE SKIP LOCKED`），先把 `next_run_at` 推到下一个周期（错过的周期不补跑），再走与手动检索同一条入队路径（配置检查、在途互斥、预算咨询锁）；客户端令牌由定时任务 id 与本次到期时间构成，重复处理不会重复入队。预算不足、有在途任务或未配置时，本次记为「已跳过」并写明原因，不重试、不排队等待。复检产生的新候选照常按 `patent_id` 去重，对话里显示「定时复检」与本次新增件数。

### 决策 14：专家名片

专家列表是前端常量，不新增端点（opportunity-center 规格不允许为占位能力造端点）。IP 专家可用；「信号洞察」（#7、#54）、「选品机会」（#55、#38）、「验证跟踪」（#13）、「商标风险」（#25）四位标「未接入」，名片写明依赖的 issue 与接入后能做什么，不能发起任务，也不发任何请求。

## Risks / Trade-offs

- **MCP 计费口径未知**：预算按调用次数控制，可能与实际计费单位（次/条）不一致。上限可调，且所有调用留痕，可事后对账。
- **检索式质量取决于大模型**：只给了已实测的语法要点；错误语法会返回 `bad_request`，用户可改后重试（消耗一次预算）。
- **国内模型的 JSON 稳定性**：输出不合格时任务记为失败（`invalid_output`），可重试；不会保存半截结果。
- **租户**：机会中心设计要求新数据带 `tenant_id`，但系统还没有租户；本期与 #24 一样按 `owner_id` 私有，租户迁移随 #13 一起做。
- **附图存库**：每件约几十 KB；调研按需取证且受预算限制，体量可控。

## Migration Plan

手写迁移建十一张表（`opp_ip_*` 十张 + `opp_workspace`），不动现有表与权限。回滚即删表。上线前在生产 `.env` 填 `OPPORTUNITY_LLM_*`（可后补）；`ZHIHUIYA_API_KEY` 已有。
