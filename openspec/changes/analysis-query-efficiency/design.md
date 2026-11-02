## Context

第一阶段（`archive/2026-10-24-post-import-query-readiness`）已交付：新期次可见前 `ANALYZE`、语句超时 30 s（异步引擎 `server_settings.statement_timeout`）、完整请求预算 25 s（`_bound_statement_to_deadline`，每条语句 `SET LOCAL statement_timeout`）、每 worker 4 个并发位与 5 s 有界排队（`search_slot`，排队前 `db.close()` 释放鉴权占的连接）。性能预算见该变更 design「性能预算」：日报 p95 ≤ 3 s、周报 p95 ≤ 5 s、单请求 25 s。

本设计依赖的代码事实（2026-10-25 核对）：

- **结果与查询者的关系**：`search()` 的 `user_id` 只喂给 `competitor_new` 筛选（`analysis_crud` 关联 `UserSellerWatch`，且以 `now()` 计算新发现窗口）。没有按用户的站点权限；`/search` 只要求 `analysis:view`，`competitor_new` 另要 `competitor:view`。
- **一页结果的数据来源不止当期**：当期事实行；趋势与历史列读回看窗口内的其他期次（`trends_stmt`、`HISTORY_OFFSETS`）；首次入榜读 `dim_keyword.first_seen_*`，会被其他期次的导入和回填脚本改写（#143 在生产直接执行 SQL）；品牌名来自 `dim_brand`。
- **默认期次在服务端解析**：请求不带日期时由 `_resolve_date(req.date, dates)` 解析为最新一期。
- **中间件是纯 ASGI**（`request_logging.py`、`request_context.py`），Starlette 0.27.0。客户端断开时处理函数不会被自动取消。
- **取消协程不停服务端语句**（#148 实测）：客户端立刻返回、连接离开了池，但 `pg_sleep(8)` 照跑满 8 s。PostgreSQL 默认只在要向客户端写数据时才发现对端已断。
- **前端**：`useAnalysisQuery` 用 `requestSeqRef` 丢弃过期响应，不中止请求。
- 生产 4 个 worker、PG 15 容器与应用同机，无 Redis 等跨进程组件。

## Goals / Non-Goals

**Goals：** 过期与断开的请求不再占用数据库；测量先行，按证据决定去重、缓存、品牌词优化与轻量保护的深度；任何共享机制都不返回过期或越权结果；每个机制可单独关闭。

**Non-Goals：** 跨 worker 的全局去重或全局并发上限；引入 Redis 等新组件；无测量依据地增加连接数或升级硬件；改动导入、导出口径；任何生产操作。

## Decisions

### D0 交付顺序与闸门

| 阶段 | 内容 | 依赖 |
|---|---|---|
| 一 | D5 测量维度（视图、结果键指纹）、D2 取消传播、D6 诊断去重 | 无，本身就是正确性或可观测性改进 |
| 二 | D1 去重、D3 缓存、D4 品牌词与计数、D7 轻量保护 | 发布后的生产重采 + 一阶段的指纹统计 |

闸门判据（重采后填入本节）：

- **去重**：同一 worker 内并发重复（同指纹、执行区间重叠）的请求占比 ≥ 5%，或拥堵时段明显更高。
- **缓存**：同指纹在 TTL 内的重复占比（跨用户）≥ 20%，且命中能把日报/周报 p95 拉回预算或显著降低。
- **品牌词与计数**：日报 p95 > 3 s 或周报 p95 > 5 s，且 `stage_ms` 显示主导阶段。
- **轻量保护**：满载压测下轻量接口 p99 超出预算。

阈值是初始建议，重采后与用户确认。未过闸门的项不实现，tasks 记录判断依据后关闭。

**重采与判断（2026-11-02，用户确认按上述阈值）**：`collect_search_latency.sh`，`SINCE=2026-10-26`，
2027 次 `/api/analysis/search`（绝大多数早于 #162 发布）。

| 项 | 实测 | 判断 |
|---|---|---|
| 日报 p95 | 0.83 s（1913 次） | 达标 |
| 周报 p95 | 25 s（96 次），超时集中在 10-27「首次入榜 + 排除品牌词」 | #162 针对的场景，发布后另行重采 |
| 同进程并发重复 | 0.7% | 去重不过闸门，不实现 |
| 600 s 内重复 | 同进程 22.8%，跨进程 32.5% | 缓存只过前半；日报已达标。同日服务路径重放：再次执行全部 ≤3.7 s，超预算只在冷缓存首次，缓存不过闸门，不实现 |
| `/api/auth/me`、`/api/analysis/scope` | p99 49 / 83 ms，最大 237 ms（含 10-27 拥堵） | 轻量保护不过闸门，不实现（实际拥堵已覆盖压测要回答的问题） |

另记：日报关键词检索偶有计数 10–13 s（常见词匹配大量关键词，约 3 次）。日报 p95 已达标，按闸门不处理。

### D1 结果键与请求去重

**结果键**（去重与缓存共用）：`(权限范围, site, view, 规范化条件, 私有标识)`。

- 规范化：用请求模型按字段名排序序列化，补齐默认值；分页、排序、每页条数都计入。
- 权限范围：目前只有 `analysis:view`；含 `competitor_new` 时为 `analysis:view + competitor:view`。权限模型以后若出现按用户的数据范围，必须进入结果键（spec 已要求）。
- 私有标识：只有含 `competitor_new` 时取 `user_id`，否则为空。非私有结果跨用户合并，是导入后集中打开默认页时收益最大的形态。
- 去重键**不含数据版本、不解析期次**：只合并「正在执行」的请求，合并到的结果最多比独立执行旧一个执行周期，可以接受；这样去重不需要在排队前多查一次库。

**合并执行（single-flight）**：

- 登记表：`dict[结果键, Flight]`，进程内、按事件循环惰性重建（同 `_search_gate` 的做法），执行结束在 `finally` 中移除，不会泄漏。
- 执行体：独立 `asyncio.Task`，自己开 `AsyncSession`、自己占并发位、自己设置 deadline，**不使用任何发起请求的会话**，避免发起者结束时会话被关闭。deadline 取创建时刻的完整请求预算，后加入的等待者共享这个截止时刻（与其各自重跑相比，代价可接受）。
- 等待者：`await asyncio.shield(flight.task)`，自己被取消不会传递到执行体；引用计数在 `finally` 中递减，归零时取消执行体，数据库侧停止由 D2 保证。
- 等待者不占并发位与连接：在进入登记表之前先 `db.close()`（同 `search_slot` 的理由）。
- 失败共享：成功与失败（`AnalysisTimeoutError`、`AnalysisBusyError` 等）都交给当时的全部等待者；结束后从登记表移除，下一个请求重新执行。
- 开关：`ANALYSIS_SINGLEFLIGHT_ENABLED`（默认值随闸门决定）。

### D2 取消传播

**前端**：`searchAnalysis` 接受 `AbortSignal`；`useAnalysisQuery` 发起新请求前中止上一个，被中止的请求不进错误态；保留 `requestSeqRef` 作二次保护。

前端实现要点（tasks 3.5）：axios 中止时抛 `CanceledError`（`code: ERR_CANCELED`），它带 `request` 没有 `response`，不拦会被全局 `errorHandler` 当成「服务器无响应」提示，所以 `errorHandler` 先放行它。ProTable 对每次 `request` 调用结束都会执行收尾、关掉加载态；被中止的调用若立即结束，新请求还在飞时加载态就没了，所以它等最新一次结束后再以 `success: false` 收尾（ProTable 对 `success: false` 既不写数据也不走 `onRequestError`）。

**断开检测**：处理函数里起一个监听任务循环 `receive()`，收到 `http.disconnect` 即取消请求任务（或在去重下递减引用计数）。FastAPI 已读完请求体，后续 `receive()` 只会返回断开消息。需要核对 `RequestLoggingMiddleware` 包装 `receive` 后此路径仍然成立。

**数据库侧停止——首选：数据库自己检测断开。** 在异步引擎 `server_settings` 加 `client_connection_check_interval`（PG14+，Linux 有效；生产和本地 Docker 都是 Linux）。请求任务被取消时，若 SQLAlchemy/asyncpg 关闭或作废了该连接的套接字，后端会在检测间隔内发现客户端已断并中止语句。

- 只作用于该请求自己的连接，没有「查进程号再取消」的竞态，不可能误伤他人。
- 代价：执行中的后端每个间隔做一次套接字轮询，开销很小；间隔建议 1000 ms，可配 `INTERACTIVE_CLIENT_CHECK_INTERVAL_MS`，0 表示关闭。
- 前提需真库验证（tasks 3.1）：取消后该连接确实被作废（而不是等语句结束后归还池），`pg_stat_activity` 在间隔内不再显示该语句。

#### 机制验证结果（2026-10-25，tasks 3.1）

环境：本地 pgbase（PG 15.19，Docker/Linux），库 `keywordiq_test`，开测时无其他会话；只执行 `pg_sleep` 与读 `pg_stat_activity`，不写表。引擎与 `database.py` 的异步引擎同形（`pool_pre_ping`、`server_settings` 含 `statement_timeout=30000`）。语句为 `pg_sleep(6)`，执行 0.5 s 后取消，另起一条 asyncpg 连接每 0.1 s 轮询 `pg_stat_activity`。

| 取消方式 | `client_connection_check_interval` | 客户端返回 | 服务端语句消失 | 被取消的连接 |
|---|---|---|---|---|
| `task.cancel()` | 未设（0） | 立即 | 5.58 s 后（跑满） | 作废，不回池 |
| `asyncio.timeout(0.5)` | 未设（0） | 0.5 s | 5.62 s 后（跑满） | 作废，不回池 |
| `task.cancel()` | 1000 ms | 立即 | 0.63 s 后 | 作废，不回池 |
| `asyncio.timeout(0.5)` | 1000 ms | 0.5 s | 0.53 s 后 | 作废，不回池 |
| `task.cancel()`，同时另一会话跑 `pg_sleep(3)` | 1000 ms | 立即 | 5 次重复：0.54–0.65 s | 作废；另一会话正常完成 |

结论：

- **首选机制成立。** SQLAlchemy/asyncpg 在任务取消时作废连接（关闭套接字），而不是等语句结束后归还；数据库默认要到写结果时才发现，加上检测间隔后在一个间隔内中止。复现了 #148 的「跑满」现象，也确认差别只在这个参数。
- **不误伤。** 只有被取消请求自己的后端停止；同池并发的另一条语句正常完成，取消后池可继续取连接执行 `SELECT 1`。
- **权限。** `pg_settings.context = user`，普通应用角色可经 `server_settings` 设置，不需要超级用户。
- **备选不需要实现**（tasks 3.2 关闭）。生产是 PG 15.8 / Linux 容器，参数自 PG 14 起提供；仍以发布后的生产链路验证（tasks 3.6）为准。
- 被作废的连接会让池少一条，下次按需新建；这是取消路径的代价，频率受断开次数约束，不影响正常路径。

**备选（仅当首选不成立）**：取消时**在归还连接之前**由辅助连接对本连接的后端进程发 `pg_cancel_backend`，并等待语句结束再释放连接。连接在取消完成前一直归本请求所有，进程号不会被他人复用，不需要也不允许「先查 `pg_stat_activity` 再取消」的两步核对。辅助连接单独 1 条、有短超时，不占业务异步池。

无论用哪种，完整请求预算（D3 of #148）都继续作为最后兜底。开关：`ANALYSIS_CANCEL_ON_DISCONNECT_ENABLED`，默认开。

#### 实现（tasks 3.3）

- `app/common/disconnect.py` 的 `cancel_on_disconnect`：查询放进子任务，同时在 `receive()` 上等 `http.disconnect`，先到者胜。断开时取消子任务并**等它退出**再返回，保证并发位释放、截止时刻还原、连接作废都发生在请求结束之前；外层被取消（优雅关闭）时子任务一并取消。
- 断开后返回 499 而不是抛异常：客户端收不到任何响应，uvicorn 对已断连接的 `send` 直接忽略；抛异常会让 uvicorn 记「Exception in ASGI application」错误栈，而断开是常态（前端每次切换条件都会中止旧请求）。
- `RequestLoggingMiddleware` 包装 `receive`：响应开始之前收到 `http.disconnect` 记 `outcome=client_disconnected`（INFO）。只认响应开始之前，流式导出中途断开仍是 `interrupted`，正常发完后 Starlette 断开监听收到的 `http.disconnect` 不计入。
- 接线在 `search_data`：鉴权与截止时刻在前，`search_slot` + `search` 作为子任务；开关关闭时直接执行，行为与第一阶段相同。
- `client_connection_check_interval` 写进异步引擎 `server_settings`，取 `INTERACTIVE_CLIENT_CHECK_INTERVAL_MS`（默认 1000，0 关闭）；同步引擎不设。

**反向代理**：nginx 默认 `proxy_ignore_client_abort off`，客户端中止时会关闭到上游的连接；需在生产链路验证（tasks 3.5），验证前不把它当作已成立。

### D3 结果缓存（闸门，默认关）

进程内 LRU + TTL。

- **缓存键**：`(结果键, 解析后的期次, 数据版本)`。期次要解析，避免默认请求在新期次导入后命中上一期。
- **不缓存**：含 `competitor_new` 的请求（私有，且依赖 `now()`）；失败结果。
- **读取顺序**：鉴权 → 一次轻量读取（数据版本 + 可选期次，同一短事务）→ 命中则返回 → 未命中则关闭会话后走去重/并发闸执行 → 以**执行前读取的版本**写入。执行期间有导入提交时，结果只登记在旧版本下，新请求读到新版本，不会命中它。
- **数据版本**：单行表 `analysis_data_version(version bigint)`，由**语句级**触发器在以下表的已提交变化中递增：`site_data_stats`（INSERT/UPDATE/DELETE，覆盖导入、重导、保留清理）、`dim_keyword`（INSERT 与 `first_seen_*` 列的 UPDATE，覆盖回填脚本与手工 SQL）、`dim_brand`（UPDATE OF name）。
  - 用事务内的普通行而不是序列：序列是非事务的，会在数据提交前就变化，导致旧数据被登记在新版本下。
  - 语句级而不是行级：`dim_keyword` 一次写几十万行，行级触发器代价不可接受。
  - 全局一个版本而不是按站点：导入每天只有几次，全部失效的代价很小，换来不会漏失效。
  - 写版本行会在该事务内持有行锁直到提交，写这些表的事务因此串行化。现有写入方（导入、保留清理、回填脚本）本就在导入锁下或离线执行；实现前核对没有交互请求路径写这些表（tasks 5.1）。读版本只是普通 SELECT，不受行锁影响。
- **兜底**：TTL（默认 600 s）覆盖不经触发器的改动，例如只改事实表的手工 SQL；在运维文档写明这类操作后需重启或等待 TTL。
- **上限**：条目数上限（默认 512）与单条大小上限（超过不缓存）。
- **配置**：`ANALYSIS_RESULT_CACHE_ENABLED`（默认关）、`ANALYSIS_RESULT_CACHE_MAX_ENTRIES`、`ANALYSIS_RESULT_CACHE_TTL_SECONDS`，同步 `.env.example` 的单位与默认值。
- 每个 worker 一份，命中率按 worker 数摊薄；指纹统计时一并估算摊薄后的命中率再过闸门。

备选：Redis/跨进程缓存。否决：新组件与运维面，对 4 个 worker 的规模不值得。

### D4 品牌词排除与计数优化（闸门）

先测量，不先改。重采后若超预算，按主导阶段选择：

- 候选 A：品牌词排除改为以首次入榜集合驱动的反连接，避免对大 `dim_keyword` 逐行判断。
- 候选 B：精确计数与取行共享同一过滤中间结果，避免重复扫描。
- 候选 C：导入时预聚合（与「聚合挪到导入时」的产品方向一致），需迁移，届时单独补设计。

约束不变：精确总数、分页边界、黑名单、首次入榜、掉榜、列表与导出一致；不截断计数、不删筛选。每个改写都要有等价性回归：同一组条件改写前后的行集合与总数一致，覆盖日报与周报。

**实测与选择（2026-10-26，生产 `d69d9d8`，`EXPLAIN (ANALYZE, BUFFERS)`）**：闸门已触发（US 周报默认冷 17.7 s、排除品牌词 13~16 s、首次入榜冷缓存超 25 s 被取消；US 日报排除品牌词约 3.4 s）。主导阶段都是计数（`count_ms`），但成因与候选 A/B 的假设不同：

- 首次入榜计数 25.2 s 中 24.6 s 是 top1 `dim_product` 左连接：计划器这条没把它消掉，15 万次冷缓存主键探测各约 0.5 ms。计数只有标题筛选需要它。
- 默认计数要整表扫 555 万行的 `dim_keyword` 建哈希（溢写临时文件），只为一个对行数没有贡献的内连接：事实行的 `keyword_id` 恒在 `dim_keyword`（导入先写维度，维度只增不删；生产核对孤立事实为 0）。
- 排除品牌词 14.2 s：288 万行 × 3 槽位逐行 `strpos` + 正则，CPU 主导；它确实要关键词文本。

第一步（不需迁移）：计数只关联筛选引用到的维度（`analysis_crud._count_joins`）。生产同 SQL 对比：US 周首次入榜 25.2 s → 0.47 s，US 周默认 9.6 s（冷）→ 3.6 s，US 日默认 0.16 s；排除品牌词不变。第二步针对品牌词（候选 C 的变体）：

- **命中对是永久属性**：「关键词以完整单词包含品牌名」只取决于关键词文本与品牌名，两者在维度上都不改。只存命中的对 `keyword_brand_hit(keyword_id, brand_id)`，普通表、只增不删、不参与保留清理。判定复用 `brand_in_keyword`（`app/table/analysis/brand_hits.py`），与逐行判定同一表达式。
- **按切片的就绪标记** `site_data_stats.brand_hits_ready`：命中表只存命中，回答不了「这个切片的对是否都判定过」。就绪的切片按主键反连接（且计数不再关联 `dim_keyword`），未就绪的照旧逐行判定，结果相同。`importer._load_facts` 删旧切片时同一事务置假，导入收尾（期次可见之后）算本切片再置真；算失败只记日志、不判导入失败。存量切片由 `scripts/backfill_brand_hits.py` 回填（倒序、每切片一事务、持导入锁）。
- **不在迁移里回填**：迁移只建空表、加常量默认为假的列（PG11+ 不重写表）。
- **生产实测**（会话临时表模拟，2026-10-26）：US 周排除品牌词计数 14.2 s → 2.35 s、第一页 0.11 s；US 日 3.5 s → 0.47 s。导入代价：US 周一期判定 40 s（44.5 万对），US 日一期 9 s（新增 1,685 对，其余周报已有）。
- **备选未采用**：事实表加布尔列——违反「事实只追加、不 UPDATE」，存量要整表重写；存未命中的对——表大一个量级，只为让导入少判已判过的对。

**发布后补充（2026-11-02，生产按服务路径重放 32 种典型查询，只读）**：

- **首次入榜取页超时：`dim_keyword` 统计滞后**。US 周报首次入榜计数 0.5 s，但第一页次次 25 s 超时。统计停在 10-21，之后的首次日期都被估成 1 行（实际 20 万），规划器先给 19 万命中行做九次主键关联再排序取 50 行（冷缓存 66 s）。`ANALYZE dim_keyword`（0.8 s）后 1.8 s。580 万行的表要 58 万行改动才自动分析，约两周一次，每个新期次都会撞上；导入收尾的统计刷新（`refresh_query_stats`）因此加上 `dim_keyword`。
- **试过、否决：取页先定本页键再补展示维度**。内层按排名顺序走索引、命中 50 行即停，但新词集中在排名末尾，要扫过切片大部分行：生产 US 周首次入榜、关键词检索均 >60 s，原语句 0.6–2.4 s。原语句从 `dim_keyword` 驱动，统计正确时反而更稳，保留原写法。
- 统计刷新后，仅首次（冷缓存）偏慢的组合：DE/GB 周「首次入榜 + 排除品牌词」13–20 s（再次 3–4 s）、US 周关键词检索 13 s（再次 1.6 s）。是磁盘读取而非计划问题，结果缓存也帮不了首次，记录不处理。

### D5 测量维度

- 请求摘要（`request_logging.py`）对 `/api/analysis/search` 额外带 `view` 与 `result_key_fp`（结果键的 SHA-256 前 16 位）。处理函数写 `request.state.summary_fields`，中间件从 `scope["state"]` 读出拼在同一条摘要末尾，不新增日志行。不用 contextvar：查询在子任务里执行（D2），子任务里设的 contextvar 传不回中间件。站点非法时只带视图（请求随后 422）。
- 指纹不可逆、不含原始条件；私有请求的指纹包含用户标识的哈希，不暴露用户。
- `scripts/collect_search_latency.sh` 增加按视图分组与按指纹估算重复率的输出：并发重复按「进程号 + 指纹」统计（去重只在进程内合并）；窗口内重复只认开始前已成功完成（`status=200 outcome=completed`）的同指纹请求，分「同进程」（缓存每 worker 一份，闸门依据）与「跨进程」（上限参考）。

### D6 诊断去重

`app/common/log_limiter.py` 的 `WindowedLogLimiter`（60 s 窗口、最多 256 个键、超限淘汰最久未出现的键），进程内一份 `diagnostic_limiter`：

- 每个键在窗口内放行首条，其余计数；下一次放行时在日志末尾报出上个窗口省略的次数。不用后台定时器补发汇总——同类不再出现时，次数仍可从逐请求的摘要得到。
- **键是 `(错误码, 路径)` 而不是 `(类型, site, view)`**：繁忙拒绝发生在排队阶段、`search()` 解析站点之前，拿不到站点；视图已随请求摘要逐条记录。
- 覆盖：`AnalysisBusyError`、`AnalysisTimeoutError`（`APIError.log_deduped = True`，改为 WARNING 不打栈——此前走 5xx 分支，每次 `logger.exception` 带完整栈，拥堵时逐请求刷栈）；语句超时（SQLSTATE 57014）的 WARNING；`search_slot` 的排队超时 WARNING。
- 不覆盖：其余 5xx（如 SSO 提供方不可用）照旧带栈；断开取消只记 INFO 摘要，不产生 WARNING；查询统计失败按批次最多重试 3 次，本身有界。
- 用户侧文案沿用既有 503 文案，不新增弹窗。

### D7 拥堵时保护轻量功能（闸门）

先压测：在每 worker 4 个分析查询满载、排队的情况下测 `/api/auth/me`、`/api/analysis/scope`。第一阶段已让排队者不占连接，预期已达标。若不达标，候选为给轻量请求保留连接（拆出独立小池）或调低分析并发位，不整体加大池；任何调整引用测量结果并保持全局连接上限算式。

## Risks / Trade-offs

- [`client_connection_check_interval` 不生效] 取决于取消后连接是否被作废；机制验证不过就改用备选，备选也不过则只保留完整请求预算兜底，并在 design 记录。
- [跨用户共享] 正确性依赖「结果与查询者无关」这一事实；以后新增依赖用户的筛选，必须同时进入结果键。在结果键构造处加断言式测试：请求模型新增字段而未声明其分类时测试失败。
- [合并放大等待] 一个慢执行让多个等待者一起等，但它们各自本来也要等同样久且不占并发位；后加入者共享先到者的截止时刻。
- [版本行锁] 写入方事务在版本行上串行化；已核对的写入方都不在交互路径上，实现前再核一遍。
- [TTL 兜底的窗口] 只改事实表的手工 SQL 最多在 TTL 内看到旧结果；默认关缓存，开启前在运维文档写清。
- [仅同 worker] 4 个 worker 下最坏仍重复 4 次，接受。

## Migration Plan

- 阶段一无迁移。新增 `INTERACTIVE_CLIENT_CHECK_INTERVAL_MS` 与各开关，默认值见上，同步 `.env.example`。
- 缓存过闸门时：Alembic 手写迁移，建 `analysis_data_version` 单行表与三组语句级触发器；降级删除触发器与表。缓存开关默认关，迁移本身不改变查询行为。

## Open Questions

- 已确认（2026-11-02）：轻量接口 p99 ≤ 1 s 与 D0 闸门阈值按初始建议执行。
- 缓存：不实现（2026-11-02 服务路径重放判断，见 D0）。
