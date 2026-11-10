## Context

- 现状：`frontend/src/pages/IP/*` 四屏都是占位页，路由挂 `canViewUnreleased`（仅超管可见），后端没有任何专利代码。业务草案见 `docs/product-monitoring-risk/01-functional-design.md`（第 7–10 节，设计稿，未实现）。执行依据是 #24 正文的「2026-11-10 已确认方向」。
- 用户决策（2026-11-10）：只用智慧芽 Data REST API，不接官方 MCP、不买网页账号、不调 AI39 外观 FTO 工作流；Key 由用户在 KeywordIQ 配置页填写，不进入聊天、Issue 或代码。
- 供应商契约（2026-11-10 按官方文档核对，精简版见 `app/table/risk/provider_contract.md`）：
  - 源站固定为 `https://connect.zhihuiya.com`，鉴权头是 `Authorization: Bearer sk-...`。另有 `apikey` 查询参数方式，官方说明它会进 URL 日志，本期不用。
  - P010 `POST /image-search/image-upload`：multipart 字段 `image`，JPG/PNG，不超过 4096 KB；返回 `data.url`、`data.expire`（32400 秒）。
  - P060 `POST /search/patent/image-single`：JSON 请求体，`patent_type=D`、`model=1`、`url` 必填；`limit` 取 1–100（默认 10），`offset` 取 0–1000；`country` 在文档里类型标为数组、说明却写逗号分隔，两者冲突。响应 `data.total_search_result_count` 与 `data.patent_messages[]`，每条含 `patent_id`、`patent_pn`、`title`、`url`、`score`、`apdt`、`pbdt`、`authority`、`loc`、`current_assignee` 等。
  - P011 `GET /basic-patent-data/simple-bibliography` 与 P013 `GET /basic-patent-data/legal-status`：`patent_id` 可逗号分隔批量，最多 100 个，按成功条数计费。
  - 响应外层为 `status`、`error_code`（0 表示成功）、`error_msg`、`data`。平台错误码 `6720xxxx`（鉴权、权限、余额、限流、过期），业务错误码 `6830xxxx`。文档未给出 QPS 和价格。
- 运行环境：生产单台 ECS，uvicorn `--workers 4`，每个 worker 都会跑后台轮询。数据库没有自动备份。

## Goals / Non-Goals

**Goals:**
- 闭环可用：配置 → 检索 → 候选 → 按需详情 → 分市场意见 → 回看。
- 付费调用只在用户显式操作时发生；刷新、重绘、轮询、并发 worker、重复提交都不会多扣费。
- 证据可追溯：每次调用的参数、范围、原始响应、时间与结果都留存；缺失、失败、未知与真无命中分开。
- 业务服务与 HTTP 层解耦，以后机会中心 Agent 可以复用同一套服务、权限和预算语义。

**Non-Goals:**
- 不做官方 MCP、Agent 编排、AI39 FTO、商标（#25）、内部案例匹配、旧 Excel 批量导入、钉钉同步、团队协作和自动持续复核。
- 不建本地全球专利索引，不镜像供应商图库，不展示供应商余额、额度或单价。
- 不给出侵权概率或自动结论，相似度只用于排序。

## Decisions

### 决策 1：Key 用 Fernet 加密，主密钥来自独立环境变量

- `RISK_CREDENTIAL_KEYS`：一个或多个 Fernet 密钥，逗号分隔。第一个用于加密，全部可用于解密（`MultiFernet`）。轮换方法：把新密钥放到最前面，在配置页重新保存一次 Key，再删掉旧密钥。
- 不复用 `ADMIN_SECRET_KEY`：那是会话签名密钥，轮换时会让所有人重新登录；两者的生命周期不应绑在一起。
- 主密钥未配置时，配置页显示「服务端未配置加密主密钥」，保存请求返回 503，检索阻断。不会退回明文存储。
- 解密失败（主密钥被换掉、密文损坏）时，配置状态显示为「凭证不可读」，检索阻断并提示超管重新保存 Key。不会把这种情况当成未配置而静默清空。
- 库里只存密文和一个指纹（`sk-…` 的末 4 位），用于界面识别是哪一把 Key。明文只在发起调用的那一刻、在内存中解密。审计记录只写动作、操作人、时间和指纹。
- 新增依赖 `cryptography`。备选是自己用 AES-GCM 实现，但 Fernet 自带版本号和完整性校验，不选自实现。

### 决策 2：原图存进数据库的独立表

- `risk_check_image` 用 `bytea` 存原图，同时记录 sha256、实际格式、大小。上传时按文件头魔数判断格式（JPEG `FF D8 FF`、PNG `89 50 4E 47`），不信任扩展名和浏览器给的 MIME。上限 4096 KB，与 P010 一致。
- 理由：单图不超过 4 MB，核查是人工逐单发起，量级小；4 个 worker 都能读，不依赖某台机器上的本地目录；不用修改 OSS 的 RAM/STS 策略（那套是为导入的 `imports/` 前缀收窄的）。
- 读取只走 `GET /api/risk/checks/{id}/image`，与核查单同样做归属校验。
- 备选 1：OSS 单独前缀。可行，但要扩 RAM 策略，而且开发和生产两套环境都要配，对这个量级不划算。备选 2：本地 `uploads` 卷。单机磁盘、没有备份，而且多 worker 之间要靠路径约定，不选。
- 代价：数据库体积会随核查单增长。一千张约 1–2 GB 的上限是可以接受的；如果以后要清理，按授权另行处理，删掉图片不等于风险解除。

### 决策 3：持久化任务表，独立轮询，`SKIP LOCKED` 原子认领

- `risk_task` 的类型有三种：`search`（必要时先上传，再检索一页）、`page`（检索指定 offset 的一页）、`detail`（对一组候选取 P011 和/或 P013）。状态有 `queued`、`running`、`completed`、`failed`、`unknown`。
- 认领：`SELECT … WHERE status='queued' ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1`，在同一个短事务里改成 `running`，写入 `lease_until = now + RISK_TASK_LEASE_SECONDS`，然后提交。**调用供应商期间不持有任何数据库事务或连接**；拿到结果后再开一个新事务写入证据并收尾。
- 轮询：`main.py` 另起一个 `run_poller([reap_risk_tasks_round, run_one_risk_task_round], RISK_TASK_POLL_SECONDS)`，不挂到导入队列上。导入批次可能占用几分钟，挂上去会让核查任务排在它后面。
- 中断：`queued` 任务重启后照常被认领，因为它还没有调用过供应商。`running` 任务租约过期时，回收器把它改为 `unknown`，**不自动重发**（扣费可能已经发生）。用户可以在详情页显式「重新检索」，这会生成一个新任务和新的调用记录。
- 认领时如果供应商已停用或未配置，任务直接记为 `failed(provider_disabled / not_configured)`，不发起调用。
- 客户端：`httpx.AsyncClient`，`base_url` 固定，`RISK_PROVIDER_TIMEOUT_SECONDS` 默认 30 秒（官方给的最大响应时间是 10 秒）。请求和异常日志只记接口编号、HTTP 状态和错误码，不记请求头。

### 决策 4：调用留痕与错误分类

- 每次 HTTP 调用写一条 `risk_provider_call`：接口编号（P010/P060/P011/P013）、关联任务、开始和结束时间、结果（`success`、`error`、`unknown`）、HTTP 状态、供应商错误码、错误分类、`billing='unknown'`。
- 判为 `unknown`（不能确定供应商是否已处理和计费）的情况：超时、连接中断、上游 5xx、返回非 JSON 或 HTML、`status=true` 但数据结构不符合契约（例如 `total>0` 而列表为空、缺少 `patent_id`）。
- 判为 `error`（供应商明确拒绝）的情况：`status=false` 或 `error_code≠0`，按错误码分类如下：

| 分类 | 错误码 / 条件 |
|---|---|
| `credential_invalid` | 67200003、67200008、67200009、HTTP 401 |
| `no_permission` | 67200004、HTTP 403 |
| `insufficient_balance` | 67200005 |
| `client_expired` | 67200006 |
| `quota_exceeded` | 67200007 |
| `rate_limited` | 67200000、67200001、67200002、HTTP 429 |
| `bad_request` | 67200012、68300004、68300007、68300010 |
| `upstream_error` | 67200100、67200101、68300005、68300006、68300008 |
| `unexpected_code` | 其他非 0 错误码 |

- 这些分类只体现在任务和调用记录上，对前端的 HTTP 响应始终是 200 或 202，所以供应商的鉴权失败绝不会表现为应用登录 401。供应商 `error_msg` 不透传给前端，只用分类映射出中文提示。

### 决策 5：法域列表在代码里维护，与站点分开

- `JURISDICTIONS` 常量包含：法域编码、中文名、供应商 `country` 取值、范围说明。首批是 US、CN、GB、DE、FR、IT、ES、JP、CA、EU（欧盟外观设计）、WO（海牙）。EU 和 WO 的供应商取值以官方状态页为准，真实联调时核实，核实前在界面上标「待核实」。
- 市场 → 推荐法域只做界面提示（例如 DE 推荐 DE、EU、WO），**由用户勾选确认**，不自动加入。界面明确说明：选了 EU 不代表覆盖成员国本国的注册。
- `country` 的传法：契约里类型（数组）与说明（逗号分隔）冲突。适配器先按数组发送，真实联调时确认；这是待联调项，模拟测试不能替代。

### 决策 6：分页与读取范围

- 首次检索的条数由用户选择 10、20 或 50，默认 20。以后每次「读取下一页」都是一个显式的 `page` 任务，`offset` 等于已读取条数，不超过 1000。
- 每页存为 `risk_search_page`：offset、limit、实际返回条数、来源总数、请求参数、原始响应。候选去重写入 `risk_candidate`（按核查单 + `patent_id` 唯一），保留首次出现的页和名次。
- 界面显示「已读取 N / 来源匹配 M」。没读取的部分不算「已排除」。
- P010 的临时 URL 记录过期时间。翻页时如果离过期不到 10 分钟，任务先重新上传（多一次 P010 调用，同样留痕），再检索。

### 决策 7：详情按需批量获取，成功的不重复取

- 用户在候选上勾选「读取著录与法律状态」，一次最多 20 件，界面提示「将产生 P011、P013 两次调用」。任务对还没有成功结果的 `patent_id` 批量调用，已有成功结果的直接跳过。
- `risk_patent_detail` 按（核查单，`patent_id`，种类）唯一，记录状态（`success`、`failed`、`missing`）、取证时间、原始数据和规范化字段。某一项失败只影响这一项，已拿到的证据保留。

### 决策 8：幂等

- 创建核查单：`client_token` 按创建人唯一。服务端对输入（字段规范化后的 JSON 加图片 sha256）计算指纹，令牌相同、指纹相同时返回原单，令牌相同、指纹不同时返回 409。
- `page`、`detail` 任务和人工意见：`client_token` 按核查单唯一，规则相同。另外，同一核查单同一时间只允许一个检索类任务处于 queued 或 running，第二个请求返回 409，防止连点重复翻页。

### 决策 9：权限

- `risk:view`：看自己的核查记录和页面。`risk:search`：发起任何付费调用（创建、翻页、详情）。`risk:review`：提交人工意见。迁移种子只授予 admin 角色（admin 默认获得全部非 super-only 权限，因此不放进 `SUPER_ONLY_PERMISSIONS`），运营和分析师需要超管显式授予。
- 供应商配置的读写和验证只认 `is_super`（`require_super`），不做成可授予的权限点。
- 归属：所有 `/checks/{id}` 系列接口按 `owner_id = 当前用户` 查询，查不到就返回 404。超管也不能隐式跨用户读取。

### 决策 10：人工意见追加保存

- `risk_review`：核查单、市场、风险判断（高/中/低/无法判断）、建议（可上架/条件通过/继续验证/需补证/不建议上架）、依据、未覆盖范围、整改条件、引用的候选 ID 列表、复核日期、签署人、时间、`client_token`。
- 只追加，不提供修改或删除接口；同一市场以最新一条为当前意见，历史全部可读。
- 校验：市场必须属于该核查单；引用的候选必须属于该核查单；「条件通过」必须同时有条件、至少一条证据引用和复核日期；检索未完成（queued、running、failed、unknown）时不能选「可上架」或「条件通过」。
- 「可上架」是 Issue 列举之外补充的选项，对应原审核表的上架建议。它只能人工选择，系统不会根据无命中自动选它。

### 决策 11：核查轮次

- 新建核查时可以选择「基于上一轮」：带入上一轮的输入，并记录 `previous_check_id`。旧意见只显示在旧核查单上，不会被带进新一轮。

## Risks / Trade-offs

- **真实 Key 未联调**：接口权益、`country` 传法、EU/WO 取值、计费方式（按调用还是按条数）、QPS 都只能在用户配置 Key 后用最少样本核实。在那之前只交付模拟联调，不宣称正式接入。
- **数据库存图让库变大**：按决策 2 的估算可以接受，以后真需要再迁到 OSS（读写都集中在一个函数里，迁移面小）。
- **`unknown` 需要人工判断**：宁可让用户看到「结果未知，可能已扣费」，也不自动重发造成重复扣费。
- **供应商附图 URL 会过期**：只记录来源和取证时间，过期后显示提示，不擅自镜像。

## Migration Plan

- 手写 Alembic 迁移：创建 `risk_*` 表，插入三个权限点和 admin 的角色授权；downgrade 删除这些。用一次性库做升级、降级、再升级，并检查模型与库的差异。
- 发布前用户需要在 ECS `.env` 写入 `RISK_CREDENTIAL_KEYS`（生成方法写在 `.env.example` 注释里）。没写也能发布，只是配置页会显示未就绪。
