## Context

现行部署形态是 **Docker Compose**（`docker-compose.yml`）：`app` 容器以
`uvicorn --workers 4` 起 4 个独立进程，`logs/` 是宿主机 bind mount
（`./logs:/app/logs`），容器重建不丢日志。仓库里没有任何 `.service` 文件——issue #93
原文提到"与现有 systemd 部署兼容"是过时表述（写于 dockerize-selfhosted-infra 变更之前），
本设计按 docker compose + host bind mount 的现状来定，不引入 systemd。

`main.py::configure_logging()` 当前用 `logging.handlers.TimedRotatingFileHandler`，
4 个 worker 进程各自 import `main.py` 时都会各建一个实例，都指向同一个物理文件
`logs/app.log`。Python 官方文档明确说明 `TimedRotatingFileHandler` 不是多进程安全的——
轮转判断（"现在是不是该轮转的时刻"）和轮转动作（rename 当前文件 + 重新打开）都在每个
进程内部独立发生，没有跨进程协调。4 个进程在午夜前后可能在毫秒级窗口内各自判断"该轮转
了"，其中几个进程 rename 成功、其余进程继续持有指向已被 rename 走的旧 inode 的文件
描述符——那些进程此后的写入去了改名后的旧文件，而不是新的 `app.log`，表现为"轮转后
部分请求的日志找不到"。

同时，现状完全没有成功请求的耗时记录（`Dockerfile` 用 `--no-access-log`），
`/api/analysis/search`（分析查询屏主查询，全站最高频的读路径）用异步引擎，而唯一的
慢查询事件监听只挂在同步引擎上，对这条路径完全不生效；nginx 侧没有自定义
`log_format`，代理层耗时与失败（502/504）无法与应用日志用同一个编号关联。

`app/common/request_context.py` 的 `RequestIdMiddleware` + `RequestIdLogFilter` 已经
是可用的基础设施（纯 ASGI、最外层、入站已带 `X-Request-Id` 时沿用），本次不重建，
只需要 nginx 侧配合与请求摘要中间件复用它。

## Goals / Non-Goals

**Goals:**
- 多进程安全的日志轮转，且能被实测验证（不是"看起来应该安全"）。
- 每个请求（成功、业务失败、未捕获异常、流式响应正常结束/中途中断）都有一条可按
  request_id 检索、跨 nginx/应用关联的摘要记录。
- `/api/analysis/search` 的慢请求能看到真实生效的作用域/筛选摘要与分阶段耗时，
  不新增查询次数、不记录检索词原文。
- 慢查询事件监听覆盖异步引擎，阈值可配置。
- 一份给未来维护者（含 issue #4 复发时）用的简短排查说明。

**Non-Goals:**
- 不接入 ELK/SLS/Loki 等日志平台，不引入消息队列。
- 不建长期指标平台、不做自动告警——那是 #90（运维巡检）的范围，本次只提供其消费的
  日志基础。
- 不修改 `api-error-contract` 的响应契约本身（形状、状态码语义、流式失败判定时机
  不变），只新增服务端日志行为。
- 不改连接池参数，不修 #4 的假定根因（issue #4 根因至今未证实，本次只补充诊断证据，
  不据此宣称解决卡死）。
- 不做自动生产发布；生产环境实际验证（磁盘余量、logrotate 真实生效、真机 nginx 配置）
  超出本会话能力范围，列为待验收。

## Decisions

### A. 日志轮转：`WatchedFileHandler` + 宿主机 `logrotate`（`create` 模式）

**决策**：应用侧不再自行判断/执行轮转。`configure_logging()` 改用
`logging.handlers.WatchedFileHandler("logs/app.log")`——它没有轮转逻辑，只在每次
`emit()` 时比较当前打开文件的 `(st_dev, st_ino)` 与最近一次打开时是否一致，不一致
（外部把文件 rename 走、又创建了同名新文件）就重新打开。轮转这件事整体移交宿主机的
`logrotate`，新增 `etc/keywordiq-logrotate.conf`：daily、`rotate 7`、`compress`、
`delaycompress`（避免正在被并发写入的当天日志被立即压缩）、显式 `create 644`（rename
旧文件后立刻创建一个新的同名空文件，而不是 `copytruncate`）。

**为什么不是 `copytruncate`**：`copytruncate` 对同一个文件描述符执行 `ftruncate`
而不重新打开，任何一个 worker 进程此刻若持有的写偏移量大于 0（几乎总是如此），
下一次 `write()` 会用该偏移量写入——在 Linux 上对普通文件而言，从当前偏移量往后写
且偏移量大于文件当前长度时，中间的空洞会被填充为 NUL 字节。多进程 + `copytruncate`
的组合会在刚轮转后的新文件里留下不定长度的 `\x00` 空洞，直到某个进程的偏移量归零
重新对齐——这本质上是另一种"轮转数据损坏"，只是表现形式不同（空洞而不是丢日志）。
`create` 模式 rename+新建是原子的目录操作，`WatchedFileHandler` 在下一次 `emit()`
就会侦测到 inode 变化并重新 `open()`（从偏移量 0 开始写新文件），没有这个问题。

**为什么不是给每个 worker 各写一个文件**（如 `app.<pid>.log`）：现有的"一次请求一个
request_id"检索方式（`grep request_id logs/app.log`）会退化成"要在 4 个文件里分别
grep"，且不知道一次请求落在哪个文件（worker 由 uvicorn 内部轮询分配，外部不可预测）。
单文件 + `%(process)d` 字段既能保留"一条 grep 命令查到一次完整请求"的体验，又能在
需要时按 pid 二次过滤，不需要为多进程定位改变检索方式。

**为什么不是引入 `concurrent-log-handler` 类第三方文件锁方案**：会给一个不到 200 行
的日志配置函数新增一个运行时依赖，且该依赖需要在 4 个进程间协调文件锁，锁竞争本身在
高频写日志场景下有可观开销。`logrotate` 是操作系统内置能力、维护该机器的人天然熟悉，
是"最轻方案"。

**代价**：轮转这件事的权威从"应用进程"移到了"宿主机 cron/logrotate"，`etc/` 下的
`.conf` 文件是模板，真正生效需要运维人工 `cp` 进 `/etc/logrotate.d/` 并确认宿主机的
`cron`/`logrotate` 定时任务本身在跑（多数发行版默认有每日 cron 触发 `/etc/cron.daily/
logrotate`，但不是所有精简镜像/裸机都保证存在）——这一步**本会话无法在生产环境验证**，
必须在 tasks.md 里列为待验收，且在 `docs/observability-runbook.md` 里写明验证方法
（`logrotate -d /etc/logrotate.d/keywordiq` 空跑不落地检查配置是否被读取）。

### B. 请求摘要中间件：三种结束形态，而非"成功/失败"二元

**决策**：新增 `app/common/request_logging.py::RequestLoggingMiddleware`，风格与
`RequestIdMiddleware` 一致（纯 ASGI class，非 `BaseHTTPMiddleware`），挂载在
`RequestIdMiddleware` **之后**添加（即中间件栈上更靠内层，`add_middleware` 后添加的
在外层，故 `RequestIdMiddleware` 必须先 `add_middleware`——这与当前顺序一致，不需要
调整），这样它能读到已经建立好的 request_id 与响应头。

包装 `send` 观察两类 ASGI 消息：
- `http.response.start`：记录 `status_code`，标记 `response_started = True`。
- `http.response.body`：若消息里 `more_body` 缺省或为 `False`，标记
  `body_completed = True`（ASGI 规范下常规响应与流式响应的"最后一块"都是这样表达的，
  不需要区分两类响应）。

`await self.app(scope, receive, send_wrapper)` 结束后按三种情况归类并输出**唯一一条**
摘要日志（`outcome` 字段区分）：

| 情况 | 判据 | outcome |
|---|---|---|
| 正常完成 | 无异常、`body_completed=True` | `completed` |
| 中途中断 | 无异常、但 `response_started=True` 且 `body_completed=False` | `interrupted` |
| 响应头发出后才炸的异常 | `self.app()` 抛出异常且 `response_started=True` | `error`（用
  `logger.exception` 记录，随后重新 `raise`，不吞异常） |
| 响应头尚未发出就炸的异常 | 理论上不会发生（全局异常处理器会在发送响应头之前把异常转成
  JSONResponse）——若真的发生，视为框架层缺陷，同样记录并重新抛出，不新增特殊分支 |

`interrupted` 这一档专门覆盖 Starlette 的 `StreamingResponse` 在检测到客户端断连时的
内部行为：它用一个 `anyio.create_task_group()` 让"产出响应体"与"监听断连"两个协程
赛跑，断连先发生时会取消产出协程，**这个取消不会作为异常传播到本中间件**——从中间件
的视角看 `self.app()` 正常返回了，但最后一次 `http.response.body` 消息的 `more_body`
仍是 `True`（生成器被取消前还没来得及发送 `more_body=False` 的收尾消息）。这正是
CLAUDE.md 里"取消/断连不能伪造正常完成"这条纪律在日志层面的对应体现——不加这一档，
所有中途断连的导出都会被误记成 `completed`。

摘要行只包含 method、`request.url.path`（**不含 query string**——GET 类列表/增长市场/
词库端点的查询参数可能带检索词或私有筛选，写入日志文件属于长期留存，比响应体一次性
传输的留存面更宽，故与"检索词不落日志"是同一条纪律的自然延伸）、status、总耗时（ms）、
`outcome`；request_id 已由 `RequestIdLogFilter` 补进每条日志记录，不重复放进消息文本，
worker/pid 同理走全局日志格式的 `%(process)d`，不重复放进这一行。

**为什么不是把这条摘要塞进 `error_handlers.py` 的各个异常处理器里**：那五个处理器
各自只在对应异常类型触发时执行，天然不会在**成功**请求上执行——要用它们覆盖"成功"，
只能在每个处理器之外再包一层，等于还是要一个中间件。且它们已经承担"错误码/detail"
这个不同的职责（面向"这次为什么失败"），与"这次请求存在过、耗时多少"这个更基础的
职责分开，两者独立演化互不牵扯，符合"不重复记录多份同义请求摘要"——这里的"摘要"
特指后者，前者是补充细节不是同义重复。

**健康检查噪音**：`/health` 被 Docker healthcheck 与 Netdata 轮询，摘要中间件对该
精确路径不输出（既有 nginx 配置已经用 `access_log off;` 做了同样的判断，两处保持
一致的决策依据）。不做更宽的"所有 GET 都跳过"之类的规则——那会把真实的慢 GET 请求
一并静默掉，与本次目标相反。

### C. nginx 是 request_id 的唯一权威生成者

**决策**：`etc/keywordiq-nginx.conf` 新增：

```nginx
log_format keywordiq_main
    '$remote_addr - [$time_local] "$request" '
    'status=$status upstream_status=$upstream_status '
    'request_time=$request_time upstream_time=$upstream_response_time '
    'request_id=$request_id bytes=$body_bytes_sent referer="$http_referer"';

access_log /var/log/nginx/keywordiq.access.log keywordiq_main;
```

并在 `location /api/` 里加 `proxy_set_header X-Request-Id $request_id;`（nginx
1.11.0+ 内置变量，每个请求一个 32 位十六进制串）。**不判断客户端是否已携带该头**，
统一由 nginx 生成并覆盖——本系统是内部工具，没有需要透传自身 trace id 的外部调用方，
统一生成规则比"存在性判断 + 两套生成来源"更简单、也消除了"外部调用方发一个超长或
含控制字符的 X-Request-Id 被一路转发"的输入面（应用侧 `_header_value` 现有的长度/
可打印字符校验因此成为纯粹的纵深防御，不再是唯一防线）。应用侧
`RequestIdMiddleware` 的"入站已带则沿用"逻辑不需要修改——行为上现在总会沿用 nginx
给的这个值，本地开发（不经 nginx 直连 8000）时行为不变（仍自行生成 8 位 hex）。

`$upstream_status`/`$upstream_response_time` 在连接失败（502/504）时是 `-`，此时
`$request_id` 仍然有值（它在 nginx 处理请求的最早期生成，不依赖上游是否响应）——
代理层失败也有可检索的编号，即使应用侧因为请求根本没到达而没有任何对应日志。

### D. 慢查询：两层，互不替代

**第一层——通用 SQL 语句级**（修复现有覆盖缺口）：把 `database.py` 里已有的
`before_cursor_execute`/`after_cursor_execute` 事件监听同时挂到 `async_engine`
（现在只挂 `engine`）。阈值从硬编码 `SLOW_QUERY_THRESHOLD = 1.0` 改为
`settings.SLOW_QUERY_THRESHOLD_SECONDS`（默认 `1.0`，写入 `.env.example`）。
这一层记录的是**单条 SQL 语句**耗时，只输出 `statement`（截断到 200 字符）不输出
`parameters`——继续沿用现状已经在做的"不记录参数值"，本次不新增参数记录，因为
`analysis` 域的查询参数常含筛选值。

**第二层——`/api/analysis/search` 专属结构化诊断**：`search()` 内部围绕既有的
六个阶段（可用日期 `_available_dates`、计数 `_listed_total`+`_dropped_total`、
取行 `search_stmt`/`dropped_stmt`、趋势回溯 `_fetch_trends`、品牌探测
`slice_has_brand_data_stmt`）各自用 `time.monotonic()` 打点（与
`import_service.py` 已有的计时习惯一致），总耗时超过
`settings.ANALYSIS_SLOW_QUERY_THRESHOLD_MS`（默认 `1000`）才输出一条 WARNING，
内容：

- 已解析的生效作用域：`site`/`view`/`on`（**解析后的真实锚定期，不是请求携带的
  草稿日期**——前端有"生效条件 vs 草稿"的区分，日志同样只能反映真正被执行的那份，
  否则排查者会拿错误的条件去复现）
- 分页（`page`/`perPage`）、排序
- 筛选白名单摘要：`has_keyword_search`（bool）、`has_asin_filter`（bool）、
  `categories_count`/`brands_count`（int）、`is_new`、`competitor_new`、`include`
  ——**不输出检索词或 ASIN 原文本身**，这是本次唯一的检索词脱敏点，与
  `api-error-contract` 里"校验错误不回显请求内容"是同一条纪律在诊断日志上的延伸
- 各阶段耗时 + `db_total_ms`（该请求内全部 `await db.execute(...)` 调用的耗时之和，
  标注"连接获取+SQL 执行，未细分"）+ `app_ms`（`total_ms - db_total_ms`，标注
  "应用处理：结果整形/序列化等"）

**"未细分"是刻意的，不是遗漏**：`db_total_ms` 把"从连接池拿到连接"与"SQL 实际执行"
合并计算，因为 `AsyncSession.execute()` 内部才会触发连接池 checkout（`asyncpg` 连接
是惰性获取的），要把两者分开需要额外挂连接池的 `checkout`/`checkin` 事件并把它们与
当前请求关联——对读路径这条连接池通常有空闲连接可用（`DB_ASYNC_POOL_SIZE=8` +
`overflow=8`），checkout 本身耗时预期是微秒级，与 SQL 执行的毫秒~百毫秒级不在同一
量级，分开测量的工程成本与诊断价值不成正比。若未来连接池经常打满导致 checkout 本身
变慢，这条判断需要重新评估，届时再补测量点，而不是现在为一个尚未观察到的问题预先
建设。

**不新增查询次数**：所有计时都是包裹既有 `await db.execute(...)` 调用的
`time.monotonic()` 差值，不新增 COUNT 或探测查询，符合 issue 明确要求。

**为什么不是复用同一个 `SLOW_QUERY_THRESHOLD_SECONDS` 给两层**：第一层是"某条 SQL
语句本身慢"（可能是索引缺失、锁等待），第二层是"整个 `/search` 调用慢"（可能是多个
本身不慢的阶段叠加，或应用层整形耗时占比异常）——两者触发不同的排查动作，共用一个
阈值会让调优其中一层时意外影响另一层的噪音水平。

### E. 排查说明文档落在 `docs/`，不落在 `CLAUDE.md`

**决策**：新增 `docs/observability-runbook.md` 而不是把内容并入 `CLAUDE.md`。
`CLAUDE.md` 是"开发约定与设计记录"（面向写代码的智能体），这份文档是"服务出问题时
按步骤查"的运维操作手册（面向排查故障的人，可能是同一个人但心智模式不同：一个是
"我要改代码，需要知道有哪些约束"，一个是"服务慢/报错了，我要在 5 分钟内定位到哪里"）。
`CLAUDE.md` 只留一行指针性引用，避免这份已经很长的文件继续膨胀。

内容大纲：
1. 拿到一个用户报错的 request_id（响应体 `request_id` 字段 / 响应头
   `X-Request-Id`）之后，如何在 `logs/app.log` 与 nginx access log 里各 grep 一次、
   如何读懂 `outcome` 字段。
2. 如何读慢查询诊断日志（两层分别长什么样、阶段耗时字段的含义、`db_total_ms`/
   `app_ms` 为什么是"未细分"）。
3. issue #4 类导入期卡死复发时，**重启前**必须采集的证据：连接池状态
   （`GET /health/pool`）、`py-spy dump` 对卡住的 worker 进程抓栈（当前会话记忆
   `import-hang-half-open-connections` 里"根因未确认，下次复发前必须先抓 py-spy 栈"
   这条要点写进这里，成为团队共享的操作步骤，不再只存在于某一次会话的记忆里）、
   `pg_stat_activity` 里是否有长期 `idle in transaction` 的连接。
4. 局限说明：本次的日志只能覆盖"请求最终完成（含失败完成）"的情形；进程被
   kill -9、或请求还没执行到任何日志点就卡死（如 #4 描述的半开连接场景），不会
   产生对应的完成日志——这不是缺陷，是"事后日志"这种手段本身的边界，能确认卡死
   发生但不能替代第 3 条的现场取证。

## Risks / Trade-offs

- **[风险] `logrotate` 依赖宿主机本身有可用的 cron/logrotate 定时任务** →
  缓解：`docs/observability-runbook.md` 给出 `logrotate -d` 空跑验证命令；
  tasks.md 把"生产环境确认 logrotate 定时任务存在且已装配置"列为独立的待验收项，
  不与代码改动的完成状态混在一起。
- **[风险] `WatchedFileHandler` 依赖 `emit()` 被调用才能检测到 inode 变化**——
  如果轮转发生后应用长时间没有新日志写入（低流量窗口），下一次写入前的这段时间里
  handler 仍持有旧 fd，但由于旧文件已被 rename、只要旧 fd 仍打开，写入内容不会丢失
  （只是暂时没有触发重新打开），等下一条日志到来时才会侦测并切换 → 不是数据丢失，
  是"切换时机略滞后于轮转时刻"，可接受。
- **[风险] `interrupted` 与 `error` 的判据依赖 Starlette 内部行为**（断连时不抛异常
  而是让协程静默返回）——这是当前 Starlette/Anyio 版本的实现细节，版本升级后若行为
  改变（比如断连改为抛出特定异常），`body_completed` 判据仍然成立（无论走哪条路径，
  "有没有发出收尾的 body 消息"这个 ASGI 层面的事实不受 Starlette 内部实现变化影响），
  故这个判据选择本身对版本升级有一定韧性。
- **[风险] 慢查询诊断的阈值/字段选择基于当前数据规模的经验判断，未在生产真实验证**
  → 缓解：两个阈值都做成可配置项，上线后可按实际观测到的耗时分布调整，不需要改代码。
- **[已知限制，非本次风险]** 生产环境实际磁盘余量、日志出口核验、logrotate 真实生效、
  真机 nginx 配置生效，均需要生产服务器访问，本次会话不具备，如实列入 tasks.md 的
  待验收清单，不在 PR 描述里宣称"已验证"。

## Migration Plan

1. 应用侧改动（日志配置、请求摘要中间件、慢查询双层、`/search` 诊断）随正常发布流程
   随镜像滚动更新，无数据库迁移、无需要停机的步骤。
2. `etc/keywordiq-nginx.conf` 与 `etc/keywordiq-logrotate.conf` 是模板文件，本次
   随代码合并，但**不自动生效**——运维需要在部署时手动：
   - `nginx -t` 校验新配置语法后 `systemctl reload nginx`（或等价的 reload 方式）
   - 把 `etc/keywordiq-logrotate.conf` 复制到 `/etc/logrotate.d/keywordiq` 并
     `logrotate -d` 空跑校验
3. 回滚：应用侧改动是纯代码回滚（镜像回退到上一版本即可，无状态迁移需要撤销）；
   nginx/logrotate 配置若发现问题可单独回退各自的文件，不影响应用容器本身。
4. 上线后第一个自然日的午夜，用 `scripts/check_logging.py`（或直接查看
   `logs/app.log*`）确认真实轮转按预期发生、旧内容完整可读。

## Open Questions

- 生产宿主机是否已经在跑标准的每日 cron 触发的 `logrotate`（多数发行版默认有，但
  取决于当前 ECS 镜像的实际配置）——需要运维在部署时确认，本次无法远程核实。
- `ANALYSIS_SLOW_QUERY_THRESHOLD_MS` 默认值 1000ms 是否合适，需要生产真实流量下的
  耗时分布数据支撑，当前只是与既有 SQL 级阈值（1.0s）同量级的经验值，上线后可能需要
  一轮调整。
