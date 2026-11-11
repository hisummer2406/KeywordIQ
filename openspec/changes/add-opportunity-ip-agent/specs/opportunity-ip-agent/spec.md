## ADDED Requirements

### Requirement: 专利调研记录按创建人私有

系统 SHALL 提供「专利调研」记录，字段为产品名称（必填）、产品说明、关键词、洛迦诺分类、检索法域（至少一项）、销售市场（至少一项，来自 `site_registry`）。记录创建后输入不可修改。每个读写都 SHALL 带当前用户的 `owner_id`，他人记录（含超管访问他人的）一律返回 404。全部端点 SHALL 挂 `opportunity:view` 权限依赖。

#### Scenario: 访问他人的调研

- **WHEN** 用户 A 请求用户 B 创建的调研详情
- **THEN** 返回 404「调研记录不存在」，与记录不存在时相同

#### Scenario: 没有机会中心权限

- **WHEN** 不持有 `opportunity:view` 的用户请求 `/api/opportunity/ip/researches`
- **THEN** 返回 403，不读取任何调研数据

### Requirement: MCP 客户端固定端点并只开放白名单工具

MCP 客户端 SHALL 只请求 `https://connect.zhihuiya.com/1458a4/mcp`，鉴权用 `Authorization: Bearer <ZHIHUIYA_API_KEY>`，`trust_env=False`。系统 SHALL 只调用 `search_patents`、`get_patent_legal_status`、`bibliography`、`abstract_image` 四个工具。Key MUST NOT 出现在响应、日志、数据库或大模型输入里。

每次工具调用 SHALL 写一条留痕：工具名、参数摘要、结果（`success`/`error`/`unknown`）、错误分类、错误码、开始与结束时间，计费状态固定为「未知」。`structuredContent.success=false` 带错误码时 SHALL 记为 `error` 并按 #24 的错误码表分类；超时、断流、HTTP 5xx、非 JSON、结构不符 SHALL 记为 `unknown`；连接失败 SHALL 记为 `error / connect_failed`。供应商错误 MUST NOT 映射为应用 401。

#### Scenario: 检索式语法错误

- **WHEN** `search_patents` 返回 `success=false`、`error_code=68300004`
- **THEN** 该检索式记为「失败 / 检索式不合法」，不记为无命中，调用计入预算已用

#### Scenario: 结果总数大于 0 但列表为空

- **WHEN** `search_patents` 返回 `total_search_result_count=30`、`results=[]`、`offset=0`
- **THEN** 该检索式记为「结果未知」，不记为无命中

### Requirement: 大模型通过 OpenAI 兼容协议接入且只做受控生成

大模型 SHALL 由 `.env` 的 `OPPORTUNITY_LLM_BASE_URL`（只接受 https）、`OPPORTUNITY_LLM_API_KEY`、`OPPORTUNITY_LLM_MODEL` 配置，任一为空即「未配置」。系统 SHALL 只在三个显式动作中调用大模型：「生成检索方案」「生成风险分析」与对话追问。大模型 MUST NOT 直接调用 MCP 或任何工具；它的输出 SHALL 经服务端结构校验后才保存，校验不通过时任务失败为 `invalid_output`，不保存部分结果。

大模型未配置时，三个 Agent 动作 SHALL 被拒绝（422）且不发起调用；人工编写检索式、检索、取证与签署意见 SHALL 照常可用。

#### Scenario: 未配置大模型

- **WHEN** `OPPORTUNITY_LLM_API_KEY` 为空，用户打开调研详情
- **THEN** 「生成检索方案」「生成风险分析」不可用并提示未配置，手动添加检索式并执行检索可以正常完成

#### Scenario: 分析引用了不属于本调研的候选

- **WHEN** 大模型输出的风险信号里有一个 `candidate_id` 不属于本调研
- **THEN** 该条信号被丢弃，其余合格内容照常保存

### Requirement: 检索式必须人工确认后才执行

Agent 生成的检索方案 SHALL 只作为建议展示，MUST NOT 自动执行。只有用户在确认框里提交检索式（至多 3 条，每条 1–500 字符，每条结果数 10/20/50）后，系统才 SHALL 入队检索任务。取证同样 SHALL 由用户选择候选与工具后显式提交。

#### Scenario: 生成方案后不确认

- **WHEN** Agent 生成了 3 条检索式，用户没有点击执行
- **THEN** MCP 调用次数不变

### Requirement: MCP 调用受单次调研与全站每日预算约束

每个会发起 MCP 调用的动作 SHALL 在入队前算出计划调用数，并在同一把咨询锁内核对：本调研已用（留痕数，含失败与未知）加在途计划加本次，不得超过 `OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH`；今天（Asia/Shanghai）全站已用加在途计划加本次，不得超过 `OPPORTUNITY_IP_MCP_DAILY_LIMIT`。超限 SHALL 返回 422，提示上限、已用与本次所需，且 MUST NOT 入队。

#### Scenario: 超出单次调研预算

- **WHEN** 单次调研上限为 20，本调研已用 18 次，用户提交 3 条检索式
- **THEN** 返回 422「本调研 MCP 调用预算不足：上限 20，已用 18，本次需要 3」，没有新任务

### Requirement: 外部调用走持久化任务且不自动重发

计划、检索、取证、分析、追问 SHALL 各自作为持久化任务由后台轮询认领；请求路径 MUST NOT 调用 MCP 或大模型。认领 SHALL 用 `FOR UPDATE SKIP LOCKED` 并写租约，执行期间 MUST NOT 持有数据库会话。同一调研同时至多一个在途任务。租约过期 SHALL 转为 `unknown` 且 MUST NOT 自动重发。同一客户端令牌同输入返回原任务，不同输入返回 409。

#### Scenario: 刷新页面

- **WHEN** 检索任务在执行中，用户刷新或重新打开调研
- **THEN** 页面读取已存状态，MCP 调用次数不增加

#### Scenario: 已有在途任务

- **WHEN** 调研有一个排队中的检索任务，用户又点击取证
- **THEN** 返回 409「已有任务在执行，请等待完成」

### Requirement: 证据留存并区分无命中、失败与未知

系统 SHALL 为每条执行过的检索式保存检索式、结果数上限、来源总数、返回条数、结果状态与原始响应；候选 SHALL 在调研内按 `patent_id` 去重并记录首次出现的检索式和名次。取证 SHALL 只对尚无成功结果的「候选 × 工具」发起调用。摘要附图 SHALL 在取证时下载（仅 `https://*.zhihuiya.com`，PNG/JPEG，不超过 2 MB）存库，经鉴权端点读取；下载失败时元数据照常保存、图片标记缺失。

界面 SHALL 把「本次检索范围内未找到」「检索失败」「结果未知」「尚未检索」分开展示，无命中 MUST NOT 显示为低风险。

#### Scenario: 一条检索式失败、一条成功

- **WHEN** 两条检索式中一条返回语法错误、另一条返回 12 条结果
- **THEN** 候选列表显示 12 条，覆盖说明显示「1 条检索式失败」，不显示「已完成全部检索」

### Requirement: Agent 风险分析与人工意见分开

风险分析 SHALL 只包含摘要、候选相关性信号、覆盖缺口、未知项与下一步建议，界面 SHALL 标明「Agent 分析，不构成法律意见」，并在旁边显示服务端计算的覆盖事实（检索式成功/失败/未知条数、已取证候选数）。Agent MUST NOT 生成、修改或预填人工意见，也 MUST NOT 给出可上架结论。

人工意见 SHALL 按市场追加保存，沿用 #24 的风险等级、建议与校验规则：市场必须在调研范围内；引用的候选必须属于本调研；没有成功执行的检索式时不能选「可上架」「条件通过」；条件通过必须填写条件、至少一条证据与复核日期。

#### Scenario: 只有失败的检索

- **WHEN** 调研的全部检索式都失败，用户签署「可上架」
- **THEN** 返回 422「检索尚未完成，只能选择继续验证、需补证或不建议上架」

#### Scenario: 先有 Agent 分析再签署

- **WHEN** Agent 分析把某候选标为高相关，用户签署「需补证」
- **THEN** 意见按用户填写保存，分析内容不变，两者分开显示

### Requirement: 工作空间按创建人私有并组织任务

系统 SHALL 提供「工作空间」：名称 1–50 字，同一用户下不重复，按创建人私有（他人访问 404）。任务（专利调研）创建时可指定所属工作空间，之后 SHALL 可移入其他工作空间或移出为「未归类」；移动不改变调研输入。工作空间下还有任务时，删除 SHALL 返回 422 且不删除任何数据。任务列表 SHALL 支持按工作空间（含未归类）筛选。

#### Scenario: 删除有任务的工作空间

- **WHEN** 工作空间里有 2 个任务，用户删除它
- **THEN** 返回 422「工作空间里还有任务，先移走再删除」，工作空间与任务都不变

#### Scenario: 移到他人的工作空间

- **WHEN** 用户把自己的任务移入用户 B 的工作空间 id
- **THEN** 返回 404「工作空间不存在」，任务不变

### Requirement: 对话追问只基于本调研证据回答

用户 SHALL 能在任务对话里追问（1–2000 字）。发送时系统 SHALL 在同一事务里保存用户消息并入队一个追问任务；同一调研有在途任务时 SHALL 返回 409 且不保存消息。大模型回答 SHALL 只基于本调研的产品信息、检索与取证证据、最新分析和最近的对话；回答 SHALL 截断到 2000 字，引用不属于本调研的候选 SHALL 被丢弃。追问 MUST NOT 调用 MCP、MUST NOT 计入 MCP 预算，回答 MUST NOT 写入或预填人工意见。界面 SHALL 把回答标为「Agent 回复，仅供参考」。

#### Scenario: 追问失败

- **WHEN** 大模型超时
- **THEN** 用户消息保留，追问任务记为「结果未知」，对话里显示「回答未生成」及原因，不显示任何回答内容

#### Scenario: 回答引用了别的调研的候选

- **WHEN** 回答的 `cited_candidate_ids` 里有一个 id 不属于本调研
- **THEN** 该引用被丢弃，回答正文照常保存

### Requirement: 定时复检只执行已人工确认过的检索式

每个调研 SHALL 至多有一个定时复检：频率每周（指定周几）或每月（指定 1–28 日），时刻 0–23 点（Asia/Shanghai），检索式 1–3 条，每条 SHALL 是本调研已成功执行过的检索式，否则 422。定时复检 SHALL 可暂停、恢复、修改与删除。

到期时后台 SHALL 先把下次执行时间推到下一个周期（错过的周期不补跑），再按手动检索同一套规则入队：未配置、在途任务或预算不足时本次 SHALL 记为「已跳过」并保存原因，MUST NOT 绕过预算或重试。同一到期时刻重复处理 MUST NOT 产生第二个任务。

#### Scenario: 用没执行过的检索式建定时任务

- **WHEN** 用户提交的定时检索式不在本调研已成功执行的检索式里
- **THEN** 返回 422「定时复检只能使用本调研已成功执行过的检索式」

#### Scenario: 到期时预算不足

- **WHEN** 定时任务到期，本调研 MCP 预算剩余 1 次，复检需要 2 次
- **THEN** 不入队任何任务，定时任务记录「已跳过：本调研 MCP 调用预算不足」，下次执行时间推到下一个周期

#### Scenario: 到期正常执行

- **WHEN** 定时任务到期，预算充足且没有在途任务
- **THEN** 入队一个检索任务，任务标记为定时复检，执行后新候选按专利去重追加
