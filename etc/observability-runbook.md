# 排障日志使用说明

面向"服务出问题了，我要在几分钟内定位到哪里"的运维操作手册。开发约定见 `CLAUDE.md`；
本次改动的设计决策与取舍理由见
`openspec/changes/add-request-diagnostics-logging/design.md`（本文档不重复那些理由，
只讲怎么用）。

## 1. 拿到一个 request_id，怎么查

用户报错时给出的编号来自两个地方：响应体的 `request_id` 字段，或响应头
`X-Request-Id`。拿到这个编号后：

```bash
# 应用日志（宿主机路径，容器内 bind mount 到同一份）。etc/keywordiq-logrotate.conf
# 配了 compress + delaycompress：当天的 logs/app.log 与轮转出的第一份历史文件
# （app.log.1）是明文，从第二份起（app.log.2.gz、app.log.3.gz……）已经是 gzip——
# 普通 grep 认不出压缩内容，找 2 天前的编号会一无所获且不会报错，看起来像是
# "这个编号在日志里不存在"（Codex 复审 PR #98 P2）。用 zgrep 统一处理，它对未压缩
# 文件的行为等同 grep：
zgrep '<request_id>' logs/app.log logs/app.log.1 logs/app.log.*.gz 2>/dev/null

# nginx 访问日志（同一个编号，跨层关联）。nginx 侧没有配 etc/keywordiq-logrotate.conf
# 这条轮转（那是应用日志专用），实际轮转策略与是否压缩以宿主机上 nginx 自带的
# logrotate 配置（通常在 /etc/logrotate.d/nginx）为准——若同样配了 delaycompress，
# 最近一次轮转出的 .1 文件也是明文，遗漏它会让"最近一次轮转"这个最常查的区间恰好
# 查不到（Codex 复审 PR #98 第三轮 P2，与上面应用日志漏过同一个坑是同一类问题）。
# 同样用 zgrep 兜底，不存在的文件模式匹配为空、不会报错：
zgrep "request_id=<request_id>" /var/log/nginx/keywordiq.access.log /var/log/nginx/keywordiq.access.log.1 /var/log/nginx/keywordiq.access.log*.gz 2>/dev/null
```

**应用日志里能看到什么**：一条"请求摘要"记录（`outcome=completed|interrupted|error|
error_before_response|cancelled`），以及（如果失败）`error_handlers.py` 记录的错误码/
细节。`outcome` 字段的含义：

| outcome | 含义 |
|---|---|
| `completed` | 正常发完响应，不代表业务一定成功——2xx/4xx/5xx 都可能是 `completed` |
| `interrupted` | 响应头已发出，但客户端断连，body 未发完（典型：导出 CSV 中途关掉浏览器标签页） |
| `error` | 响应头发出后才抛出的未处理异常（如导出生成器中途报错），`status` 是响应头里已经确定的那个状态码 |
| `error_before_response` | 响应头发出前就抛出的异常，且没有被 `APIError`/`RequestValidationError`/`StarletteHTTPException`/`SQLAlchemyError` 这四类处理器截住——它固定记 `status=500`：这类异常最终会被 `error_handlers.py` 给裸 `Exception` 注册的兜底处理器接住并转成 500 发给客户端，只是那个处理器在本中间件的可见范围之外，看不到那次发送动作。看到这一档不代表框架有缺陷，只代表"这是一次没被具体错误类型覆盖到的未预期异常"，值得去看栈本身（同一条日志的 traceback）而不是怀疑日志记录逻辑 |
| `cancelled` | 请求所在的 Task 被取消（Uvicorn 优雅关闭超时、应用层超时等），不是客户端主动断连——若命中的是 `/api/analysis/search` 且已经超过慢查询阈值，同一个 request_id 下应该还能找到一条带 site/view/filters/stage_ms 明细的"分析查询慢请求诊断"（`request_cancelled=True`），比这条通用摘要更有诊断价值 |

**nginx 日志里能看到什么**：`status`（nginx 返回给客户端的状态码）、
`upstream_status`（应用实际返回的状态码，502/504 时为 `-`）、`request_time`（nginx
从收到请求到发完响应的总耗时）、`upstream_time`（nginx 等待应用响应的耗时）。两者之差
约等于"耗时花在网络/nginx 排队"而非"应用处理"。

**代理层失败（502/504）**：nginx 日志里 `request_id` 仍然有值（它在请求最早期生成，
不依赖上游是否响应），但应用日志里不会有对应记录——请求根本没有到达应用。看到
`upstream_status=-` 时，先怀疑应用容器是否在跑、连接池是否耗尽（`GET /health/pool`），
而不是去应用日志里找一条永远不存在的记录。

## 2. 怎么读慢查询诊断

两层日志，触发条件与内容都不同：

- **通用 SQL 级**（`慢查询检测 (Xs): <SQL 前 200 字符>`）：某一条 SQL 语句本身慢，
  同步与异步引擎都会记（`SLOW_QUERY_THRESHOLD_SECONDS`，默认 1 秒）。只有语句文本，
  没有参数值——看到这条要去数据库侧查执行计划（`EXPLAIN ANALYZE`）或看是不是在等锁
  （`pg_stat_activity` 里的 `wait_event`）。
- **`/api/analysis/search` 专属诊断**（`分析查询慢请求诊断 ...`）：整次调用慢
  （`ANALYSIS_SLOW_QUERY_THRESHOLD_MS`，默认 1000ms）。字段含义：
  - `site`/`view`/`on`：**实际生效的作用域**，不是请求携带的草稿值——按这个去复现，
    不要按用户描述的"我选的是昨天"去复现（前端有生效条件与草稿的区分，两者可能不同）。
  - `filters`：白名单摘要（`has_keyword_search`/`has_asin_filter`/`categories_count`/
    `brands_count`/`is_new`/`include`），**不含检索词或 ASIN 原文**——
    不能靠这条日志反查用户具体搜了什么，只能看"是不是带了筛选、带了几类"。
  - `stage_ms`：`scope_ms`（解析可用日期与锚定期）/ `count_ms`（计数）/
    `rows_ms`（取行）/ `trend_ms`（趋势与历史排名回溯）/ `brand_probe_ms`（品牌数据
    探测），每项都是包裹既有查询的耗时，没有额外发起的探测查询。**每个阶段内部仍然
    混着数据库往返与纯 Python 的结果整形**（如 `rows_ms` 既含 SQL 执行也含
    `shape_rows`），只用来定位"大概是哪一步慢"，不能拿单个阶段的数字当成"这一步全是
    数据库耗时"。
  - `db_total_ms`（仅 `await db.execute(...)` 的等待时间，含连接池 checkout）与
    `app_ms`（其余应用处理：结果整形/序列化等）：两者相加等于 `total_ms`。这两个数
    是独立累加的（`_timed_execute` 包裹每一次 `db.execute` 调用），不是靠
    `stage_ms` 求和推出来的——早期实现犯过这个错误：把阶段耗时求和当成"数据库耗时"，
    而阶段里混着纯 Python 的整形工作，导致 `db_total_ms` 恒等于 `total_ms`、
    `app_ms` 恒等于 0，诊断从来指不出问题真的出在整形还是数据库（PR #98 Codex 复审
    修复）。看到 `db_total_ms` 占比高，先查 `GET /health/pool` 看连接池是否接近打满
    （checkout 排队本身会拖慢这个数字），或去数据库侧查执行计划；看到 `app_ms` 占比
    高，怀疑是应用进程本身在争抢 CPU（4 个 worker 共享 4 vCPU），或是结果集本身偏大
    导致模型构造/序列化变慢。

低于阈值时两层都不输出，这不是缺陷——阈值以下的请求不需要诊断。

**`on=None` 的诊断行**：站点/视图确实没有任何数据时 `/api/analysis/search` 会提前
返回空结果，但如果这个判定过程本身很慢（`scope_ms` 偏高，通常是连接池排队或数据库侧
锁等待），同样会输出这条诊断，只是 `on` 字段是 `None`、后面几个阶段没有值——看到
`on=None` 不代表数据丢了，只代表"判断没有数据"这件事本身耗时超过了阈值。

**`request_failed`/`request_cancelled` 字段**：请求最终失败（`request_failed=True`）
或被取消（额外带 `request_cancelled=True`，如 Uvicorn 优雅关闭超时）也照常输出这条
诊断——这两种恰恰是连接卡死/锁等待（issue #4 那一类）最可能触发的形态，此前这里一旦
出异常就直接跳过诊断，是诊断盲区里最不该漏的一块。看到这两个字段为 `True` 时，
`db_total_ms`/`stage_ms` 仍然可信（记录的是"失败/取消前已经等了多久"），但
`app_ms`/`total_ms` 不再代表"成功处理完一次请求的总耗时"。

## 3. issue #4 类"导入期卡死"复发时，重启前先采集这些证据

根因至今未证实（见项目记忆 `import-hang-half-open-connections`：已用 TCP keepalive
参数压住了一次已知触发路径，但不能排除还有其他路径）。**如果直接重启应用容器，
所有能证明"发生了什么"的现场都会消失**，下一次复发时又要从零开始猜。重启前依次采集：

1. **连接池状态**：`curl -s http://127.0.0.1:8000/health/pool`（或从 nginx 外部访问
   `/api` 前缀下的等价路径，取决于是否已配置反代）——记录同步/异步两个引擎的
   `checked_out`/`overflow` 是否已经打满。打满且长期不释放是"半开连接把 worker
   挂死"的直接证据。
2. **对卡住的 worker 进程抓 Python 调用栈**：先用 `docker compose exec app ps aux`
   或宿主机 `ps` 找到卡住的 worker pid（应用日志的 `pid=` 字段能帮助定位哪个 worker
   最后有日志、哪个已经沉默），再
   ```bash
   docker compose exec app py-spy dump --pid <pid>
   ```
   （需要容器内装有 `py-spy`，如果没有装，记录下这一步做不到，也是一条有用的信息——
   下次部署时应该预先装好）。栈顶如果卡在 `psycopg2`/`socket` 的读操作，指向的是
   连接层面的半开连接；如果卡在应用代码的某一行，指向的是别的问题。
3. **数据库侧的连接状态**：连进 `db` 容器执行
   ```sql
   SELECT pid, state, wait_event_type, wait_event, query_start, state_change, query
   FROM pg_stat_activity
   WHERE datname = current_database()
   ORDER BY query_start;
   ```
   重点看 `state = 'idle in transaction'` 且 `state_change` 是很久以前的行——这类
   连接持有着锁或连接池名额却什么都不做，是"应用以为连接还活着、数据库这边其实已经
   看不到对端"的另一种表现。
4. 以上三步的输出**先保存到文件**（复制粘贴到一个临时文件即可，不需要精致的工具），
   再重启容器恢复服务。没有这一步，下一次复发时同样的调查要重新做一遍。

## 4. 局限：这套日志只能覆盖"请求走到了日志点"的情形

本次的请求摘要中间件、慢查询诊断，全部是在**请求处理过程中**的某个时间点主动写日志。
两类情形不会产生任何对应记录：

- **进程被 `kill -9`**：日志缓冲区里尚未落盘的内容直接丢失，且请求本身也不会走到
  "记录摘要"那一步（连 Python 异常处理都不会触发）。
- **请求卡在半开连接或死锁上，永远不返回**：既没有失败也没有成功，中间件的
  "请求结束时记录"这一步永远不会执行——这正是 issue #4 描述的场景本身。

这不是本次改动的缺陷，是"事后日志"这种手段的固有边界：它能告诉你**已经发生并且走完
了流程的事情**是什么样，不能替代对**还没走完流程、卡在半路**的现场取证（见第 3 节）。
换句话说：日志能确认"发生过卡死"，不能替代抓栈/查连接状态去确认"卡在哪里"。

## 5. 采集分析查询的耗时分布（基线 / 发布后对比）

要回答「分析查询现在有多慢、发布后有没有变好」，用 `scripts/collect_search_latency.sh`。
它只读：只读应用日志，SQL 在只读会话里执行。输出包括：
- `/api/analysis/search` 按日与合计的完整请求耗时分位数，以及超过 3 s / 5 s / 25 s 预算的比例；
- 轻量接口（`/api/auth/me`、`/api/analysis/scope`）作对照；
- 慢请求诊断的视图拆分与最慢 25 条；
- 数据规模与统计新鲜度。

```bash
# 在本机执行，脚本经 stdin 传到 ECS（访问方式与授权每次另行确认）
ssh <user>@<host> 'bash -s' < scripts/collect_search_latency.sh > baseline.txt
# 只看发布之后：SINCE 为日志日期下限
ssh <user>@<host> 'SINCE=2026-11-01 bash -s' < scripts/collect_search_latency.sh > after.txt
```

应用日志时间是北京时间（Compose 给 app 设了 `TZ=Asia/Shanghai`）；在此之前的日志是 UTC，跨这个
发布点按日期切分时边界相差 8 小时。

- **口径**：应用日志「请求摘要」里的 `duration_ms`。V2 的 nginx 用默认 combined 格式，不记耗时，不能作为来源。请求摘要不带视图，所以第 1 节是日报、周报合计；要按视图拆，看第 3 节的慢诊断。
- **日志文件名**：V2 应用日志按 `app.log-YYYYMMDD(.gz)` 轮转，当天的是 `app.log`，与第 1 节示例中的 `.1/.N.gz` 不同。脚本用 `logs/app.log*` 一并读取。
- **修复前基线**与**性能预算**（日报 p95 ≤ 3 s、周报 p95 ≤ 5 s、单请求 ≤ 25 s）记录在 `openspec/changes/archive/2026-10-24-post-import-query-readiness/design.md`，见其中「生产基线」「性能预算」。
