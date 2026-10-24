## Context

事实表两级分区：月 RANGE → 站点 LIST（日/周），掉榜与增长表是月 RANGE 叶子。查询按 `(site, report_date)` 裁剪到单个叶子分区，计划质量取决于该叶子的列统计。

导入收尾顺序（`_execute_locked`）：事实事务提交 → `_update_site_data_stats`（新期次进入 `available_dates`，对用户可见）→ `_rebuild_filter_stats` → 批次 `COMPLETED`。全程没有 `ANALYZE`。

### 实验证据（2026-10-22，本地 pgbase PG15.19，生产为 15.8）

一次性库 `keywordiq_exp148`；5 月叶子分区与维度表关闭 autovacuum 以控制统计状态；查询由 `analysis_crud` 的现有语句构造函数生成。

| 场景 | 默认 count+rows | 排除品牌词 | 首次入榜 |
|---|---|---|---|
| R1 新分区未分析（05-03） | 946 + 12575 ms | 5658 + 17037 ms | 0 ms（当期为空） |
| R1 ANALYZE 后 | 171 + 9 ms | 1328 + 9 ms | 5 ms |
| R2 有统计、不含新期（05-04） | 729 + 17572 ms | 8345 + 22753 ms | 两条均 >120 s 被取消 |
| R2 仅 ANALYZE 日报叶子（0.65 s） | 250 + 22 ms | 1020 + 9 ms | 317 + 811 ms |

R2 关键节点：`fact_daily_2026_05_us` 估计 1 行、实际 377,060 行，外层随之选择 Nested Loop。维度表未重新分析也全部恢复，说明决定性因素是事实叶子分区。掉榜叶子同样估 1 行/实 88,356 行（当前 169 ms），一并分析，成本很低。

周报没有本地样本，未测；机制相同（`report_date_week` 同为裁剪列）。

自动分析触发条件是 `n_mod_since_analyze > 50 + 0.1 × reltuples`。日报约 45 万行/期，月内第 11 天起单日导入低于阈值，统计会滞后 1–3 期。

### 生产基线（2026-10-24 只读采集，修复前代码）

来源：生产应用日志 10-18 至 10-24 的「请求摘要」（每个请求的 `duration_ms`）与「分析查询慢请求诊断」，以及只读 SQL（只读事务 + 20 s 超时）。V2 的 nginx 用默认 combined 格式，不记耗时，所以完整请求耗时以应用的请求摘要为准。

数据规模（`site_data_stats`）：

| 站点 | 日报期数 | 日报每期行数（均 / 最大） | 周报期数 | 周报每期行数（均 / 最大） |
|---|---|---|---|---|
| US | 39 | 42.9 万 / 64.4 万 | 6 | 284.5 万 / 320.0 万 |
| DE | 39 | 6.7 万 / 11.9 万 | 6 | 49.6 万 / 59.2 万 |
| GB | 39 | 6.1 万 / 9.9 万 | 6 | 43.2 万 / 49.8 万 |

`dim_keyword` 554.6 万行，本地实验库只有 54.6 万。本地实测只能证明机制，不能代表生产耗时。

`/api/analysis/search` 完整请求（n=1916）：p50 0.65 s、p90 321 s、p95 1343 s、p99 5850 s、最大 7884 s。超过 3 s 的占 21.0%，超过 30 s 的占 14.8%；返回 500 的 83 次。

| 日期 | n | p50 | p95 | 最大 |
|---|---|---|---|---|
| 10-21 | 420 | 0.70 s | 1.2 s | 16.7 s |
| 10-22 | 363 | 0.19 s | 1190 s | 1650 s |
| 10-23 | 180 | 0.54 s | 1376 s | 1463 s |
| 10-24 | 369 | 0.97 s | 5851 s | 7884 s |

- **10-22 起的长尾就是本变更的根因形态。** 最慢的 25 条诊断全部是日报 `on=2026-10-22`、勾选首次入榜与排除品牌词，计数和取行两个阶段各约 50 分钟。`fact_daily_2026_10_us` 最近一次 autoanalyze 是 10-21，10-22 导入后这一期不在统计里，与实验 R2 一致。
- **轻量接口被拖垮。** `/api/auth/me` p95 12.4 s、p99 30.0 s；`/api/analysis/scope` p99 30.0 s。异步池被慢查询占满后，其他请求一直等到 `pool_timeout`（30 s），这正是 D3/D4 要防的形态。
- **统计新鲜时的常态。** 10-21 日报 p95 1.2 s。
- **手工 ANALYZE。** `fact_daily_2026_10_us` 的 `last_analyze` 是 2026-10-24 09:04 UTC，有人手工执行过，不是本变更所为。采集时（14:42 UTC）数据库没有活动查询。
- **遗留批次。** `import_batch` 没有 `PROCESSING` 批次，D2 中「迁移前遗留批次会被判失败」在生产上不会命中。
- **周报未单独统计。** 请求摘要不带视图，周报的分布没有单独统计；慢诊断 654 条中周报只有 24 条。

### 性能预算（2026-10-24 用户确认）

口径：`/api/analysis/search` 的完整请求耗时（含鉴权与序列化），以请求摘要的 `duration_ms` 统计，冷热缓存都计入。

- 日报 p95 ≤ 3 s，周报 p95 ≤ 5 s。
- 单请求上限 25 s，低于语句超时 30 s，留出余量。完整请求预算尚未实现，是第一阶段剩余验收项。
- 验收方法：部署后用同一口径重采，与上表对比，日报、周报分开统计；周报统计需要请求摘要或诊断能区分视图。
- 建议（待确认）：轻量接口（`/api/auth/me`、`/api/analysis/scope`）p99 ≤ 1 s，分析查询拥堵时也不退化。

## Goals / Non-Goals

**Goals：** 新期次一经可见即有正确统计；统计失败不丢数据、不显示为「无数据」，并能自动恢复；交互查询有硬执行上限、并发有界，慢查询不再拖垮其他页面。

**Non-Goals：** 去重/取消/缓存/计数优化（#148 后续）；调大连接池；生产补 `ANALYZE`；修改业务数据。

## Decisions

### D1 先 ANALYZE 再可见

在事实事务提交后、`_update_site_data_stats` 之前，对本次写入的叶子分区执行 `ANALYZE`。范围是 `fact_daily|weekly_<月>_<站点>`、`fact_dropped_<月>`、`fact_growth_<月>`，表名经 `partitioning` 的命名函数取得。

- 放在可见之前：用户第一次能选到这个期次时，计划已经正确。`_rebuild_filter_stats` 本身也查这个切片，同样受益。
- 只分析叶子分区：查询裁剪到叶子，实验证明叶子足够。父分区表 autovacuum 不处理，但当前查询不依赖它的统计。
- 不分析维度表：维度表很大且由 autovacuum 维护，实验显示不需要。如果以后计划显示需要，再单独加。
- `ANALYZE` 只取 ShareUpdateExclusive 锁，不阻塞读写；可在事务内执行。用 `SET LOCAL statement_timeout` 限时，默认 300 s，可配 `IMPORT_ANALYZE_TIMEOUT_SECONDS`。
- 同一期重导走同一路径。

备选：只调低这些分区的 `autovacuum_analyze_scale_factor`。否决，因为它仍是异步执行，无法保证「可见前已分析」，也给不出失败状态。

### D2 统计状态与恢复

`import_batch` 新增 `stats_status`（String(16)，可空）与 `stats_attempts`（SmallInteger，默认 0）。

- NULL：迁移前的旧批次，或未成功导入的批次，不适用。
- `PENDING`：事实已提交，统计尚未成功。
- `READY`：已成功。
- `FAILED`：重试用尽。

流程：事实提交后置 `PENDING` → `ANALYZE` 成功置 `READY`，失败 `attempts+1` 并记日志（`logger.exception`）。无论成败，都继续写 `site_data_stats` 并置 `COMPLETED`：数据已入库，不能误报为不存在。

恢复：导入轮询新增维护性 job，非阻塞尝试 `import_lock`。拿到锁时说明没有导入在跑，此时 `PENDING` 一定是失败或中途崩溃留下的，按批次重试；`attempts` 达到 3 置 `FAILED` 并停止。拿不到锁就跳过本轮，不等待。`FAILED` 之后由 autovacuum 最终兜底，或由运维手工处理。

中途退出（PR #149 Codex 复审 P2）：事实与 `PENDING` 同一事务提交之后、批次置 `COMPLETED` 之前进程退出，批次会停在 `PROCESSING`。排队接手只看 `QUEUED`、上传回收只看 `UPLOADING`，没有别的东西回头看它，这一期事实已入库却永远不可见。

- 判据：持有 `import_lock` 时（`PROCESSING` 只在 `_execute_locked` 持锁时写入，全仓唯一写入点），任何 `PROCESSING` 都是中断留下的。`stats_status` 非空：事实已提交、退在收尾，补完。为空：退在事实事务里、已整体回滚，判 `FAILED`（「导入进程中断，导入未完成；请重导该批次」）。
- 判失败而不是放着：否则记录永远显示处理中、不能重导（重导只收 `FAILED`），ops-bot 也把它当作在跑的导入（`ops_bot/checks/import_status.py`），重启与发布一直被挡。
- 迁移前就停在 `PROCESSING` 的旧批次 `stats_status` 也为空，部署后第一轮同样被判失败。旧代码无从区分事实是否已提交，所以提示语不写「数据未入库」。判失败只改批次记录，不动事实；重导先删切片再写。
- 补完：同一个维护 job 先处理这类批次，再重试 `COMPLETED + PENDING`。正常收尾与补完共用 `_finish_committed_import`（刷新统计 → `site_data_stats` → 筛选统计 → `COMPLETED`），已 `READY` 的不再重复 `ANALYZE`。
- 计数：在榜行数、源文件行数与跳过说明随事实同一事务落到批次上（供历史展示）。补完时发布的在榜、掉榜行数一律取切片现状 `count(*)`，不取批次上的值：退出到接手之间，同一期可能已被排在前面的排队接手或直接提交重导，分区也可能已被保留清理移除（PR #149 Codex 第二轮复审）。
- 数据已不在：切片在榜行数为 0 而批次当初写过行，不发布 `site_data_stats`，批次判 `FAILED` 并写明原因。
- 补完再失败：与正常收尾失败同样判 `FAILED`、`stats_status` 清空，不无限重试。

- 有界：每批最多 3 次，日志随之有界。
- 重试复用 D1 的同一函数（按批次的 site/period/report_date 推导分区）。
- 同一期被后续重导时不特殊处理：旧批次重试分析的是同一组分区，结果同样有效且无害。

前端：`ImportStatusCell` 在 `COMPLETED` 且 `stats_status ∈ {PENDING, FAILED}` 时，在 Tag 下显示说明，例如「查询统计待更新，可能暂时较慢」「查询统计更新失败，查询可能较慢，请联系管理员」。

### D3 交互查询执行超时

异步引擎 `connect_args.server_settings["statement_timeout"] = INTERACTIVE_STATEMENT_TIMEOUT_MS`（默认 30000）。

- 作用于全部交互读路径。同步引擎（导入、导出、迁移）和 ops-bot 自有引擎不受影响。
- 服务端超时会取消执行并释放该连接上的工作；而 `pool_timeout` 只限制等待连接的时间。
- 超时的 SQLSTATE 是 57014，映射为 503 `db.query_timeout`「查询超时，请缩小筛选范围后重试」，记 WARNING，不打栈。其余 `OperationalError` 仍按原逻辑处理。asyncpg 经 SQLAlchemy 包装后的异常类型以真库测试为准。

#### 完整请求预算（第一阶段剩余验收 7.2）

`/api/analysis/search` 的完整请求预算由 `ANALYSIS_REQUEST_TIMEOUT_SECONDS` 设定，默认 25 s，见「性能预算」。预算从处理函数入口、排队之前起算，排队等待也计入；鉴权依赖在它之前执行，不计入。

- **不能只在应用层放弃等待。** 实测经 SQLAlchemy 的 asyncpg 适配层用 `asyncio.timeout` 取消协程后，客户端立刻返回，连接也离开了池，但服务端的 `pg_sleep(8)` 仍执行满 8 s。也就是说，这种做法只是把请求和连接还给了应用，数据库照样在跑，上限仍然只有引擎级的 30 s。
- **做法：每条语句由数据库按剩余预算取消。** search 内的每条语句都经过 `_timed_execute`，执行前先发 `set_config('statement_timeout', 剩余毫秒, true)`（即 `SET LOCAL`，只作用于本事务，且不超过引擎级的 30 s），到点由数据库取消。
- **预算用尽的处理。** 执行前发现预算已用尽，就不再发起查询，直接抛 `AnalysisTimeoutError`。
- **对外表现。** 两种情况都返回 503 `db.query_timeout`。对用户来说，语句超时和请求超时是同一件事，所以用同一个码和同一段文案。
- **截止时间的传递。** 截止时刻放在 ContextVar 里，由 `search()` 设置，退出时还原。`get_scope` 等不在 search 内的查询读到 None，不受约束。
- **代价。** 每条语句多一次 `set_config` 往返，一个请求 4–6 条语句；应用与数据库同机，这次往返相对查询本身可以忽略。
- **测试覆盖。** 真库测试验证三点：到点由数据库取消（SQLSTATE 57014）、服务端没有残留语句、连接可以继续使用，事务结束后 `statement_timeout` 回到引擎级的值。

### D4 分析查询并发上限

`/api/analysis/search` 用进程级 `asyncio.Semaphore(ANALYSIS_MAX_CONCURRENT_SEARCHES=4)`，获取等待 `ANALYSIS_SEARCH_QUEUE_TIMEOUT_SECONDS=5`。等不到返回 503 `analysis.busy`「查询繁忙，请稍后重试」。

- 异步池为每 worker 8+8。分析查询最多占 4 条，剩余连接留给鉴权、作用域、词库等轻量请求。
- 排队有界：等待者在超时后放弃，不形成无界积压。客户端断开后取消正在执行的查询属于 #148 后续，本变更依靠 D3 的超时兜底。
- 信号量包住整个 `search()` 调用，因为一次请求顺序执行 4–6 条 SQL。
- 排队前先 `close()` 请求会话（PR #149 Codex 复审 P1）：鉴权依赖与 search 共用同一个请求会话，鉴权查询留下的事务攥着一条连接直到请求结束；不还的话 4 个在跑加 12 个在排就耗尽 8+8 的池。`close()` 等价于请求结束时的收尾（不提交），已载入的用户对象不过期，会话之后可继续用。鉴权仍在闸之前，未登录请求不占排队位。

## Risks / Trade-offs

- [30 s 误杀合法慢查询] 实验中 ANALYZE 后最慢为 1.3 s；生产数据量更大。阈值可配，命中时记 WARNING 带诊断，上线后观察。
- [ANALYZE 延长导入] 实测 0.65–0.95 s/期，相对 70–120 s 的导入可以忽略。
- [信号量按 worker 计] 4 个 worker 共 16 个并发，不是全局上限；全局上限需要跨进程协调，不值得。
- [statement_timeout 作用于异步路径上的写操作] 现有异步写都是单行级操作；若将来有长写，应走同步引擎（`async-request-execution` 已有约束）。

## Migration Plan

Alembic 手写迁移：`import_batch` 加两列，可空/默认 0，不回填。降级直接删列。部署后新导入即生效。已在线期次不处理（见 proposal Impact）。

## Open Questions

无阻塞项。生产统计实况（各叶子 `last_autoanalyze` 与导入时间的差距）可以用 diagnose-v2 只读核对，但需要用户单独要求。
