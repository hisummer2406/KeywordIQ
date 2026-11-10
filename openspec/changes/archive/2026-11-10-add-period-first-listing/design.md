## Context

现行 `keyword-first-seen` 为每个 `(站点, 关键词)` 存一份日周共享的首次记录（`dim_keyword.first_seen_date / first_seen_period_type / first_seen_basis`），查询时用 `analysis_crud.first_listed_expr` 按日周映射派生「首次入榜」。#178 要补一个口径：**首次进入当前榜单**——日视图问「第一次上日榜」，周视图问「第一次上周榜」，两个周期互不影响。

生产现状（2026-11-10 只读）：三站日报 2026-09-14～11-08 连续 56 期，周报 2026-09-12～11-07 连续 9 期，事实表最早期次 = 最早 COMPLETED 批次，无清理分区；`dim_keyword` 约 620 万行、2.2 GB（含 trigram GIN）。US 日报每期约 42 万行，周报约 286 万行。保留清理 2026-12-01 03:30 起移出 9 月分区。

## Goals / Non-Goals

**Goals：** 按周期独立的永久首次证据；导入与回填结果与顺序无关；查询、数量、导出、缓存同一口径；旧 API、旧保存条件原义不变；12-01 前可在生产精确重放。

**Non-Goals：** 改共享首次记录语义；Amazon 全历史首次；增长判定；从 OSS 原始 CSV 重放（事实表证据被清理后的恢复不在本变更内）。

## Decisions

### D1 存储：`dim_keyword` 加两组列，不建新表

新增 `day_first_date date NULL`、`day_first_basis varchar(20) NOT NULL DEFAULT 'UNKNOWN'`、`week_first_date`、`week_first_basis`。

- 一词一周期一条值，与 `dim_keyword` 一一对应；放同一行，查询沿用已有的 `DimKeyword` 关联，不多一次连接。
- 替代方案「独立表 (keyword_id, period_type)」：回填不必改写 `dim_keyword`，但每个查询多一次 620 万行级连接，且导入要多维护一张表的一致性。回填改写只发生一次（随后 VACUUM），选列方案。
- 迁移：`ADD COLUMN ... DEFAULT 'UNKNOWN'` 是常量默认，PG ≥ 11 只改目录、不重写表；随后 `CREATE INDEX`（非 CONCURRENTLY，与现有迁移一致）建两个部分索引 `(site, day_first_date) WHERE day_first_basis='IMPORTED'`、`(site, week_first_date) WHERE week_first_basis='IMPORTED'`。迁移时全为 UNKNOWN，部分索引为空，建索引只是一次顺序扫描（2.2 GB，秒级到一分钟）。全部对象在 `analysis` schema，迁移显式写 `schema="analysis"`；downgrade 删索引与列。

### D2 来源等级：增加 `ABSENT`（已确认该周期未出现）

周期证据的等级为 `IMPORTED` / `EARLIEST_OBSERVED` / `UNKNOWN` / `ABSENT`。`ABSENT` 只出现在周期字段，日期为空，表示「系统确知它从未出现在该周期的任何已导入报告中」。

没有它就无法区分两件事：周报新建的词（日报确实没见过）与迁移前的存量词（不知道见没见过）。只有前者在日报第一次出现时可以记为 `IMPORTED`。这正是 #178 样例的形态（先上周榜后上日榜）。

导入对周期 `P`、报告日 `r` 的规则（与共享记录同一事务、同一条 staging 来源）：

| 当前周期等级 | 写入 |
|---|---|
| 行不存在（新词） | `P` = (`r`, IMPORTED)；另一周期 = (NULL, ABSENT) |
| ABSENT | (`r`, IMPORTED) |
| UNKNOWN | (`r`, EARLIEST_OBSERVED) |
| IMPORTED / EARLIEST_OBSERVED | 仅 `r < 当前日期` 时把日期前移，等级不变 |

相等不写（与 `first_seen_advance_sql`、`dim_product` 同一条纪律：每次 UPDATE 都在 GIN 上插新条目）。顺序无关性：同周期内比较键只有期次日期；跨周期只有「新建行时另一周期记 ABSENT」，而 ABSENT 遇到任意期次都转为 IMPORTED 并取最早——所以无论日、周谁先到，结果都等于按时间顺序导入的结果。用全排列真库测试证明（tasks 3.x）。

### D3 周期监测起点与命中

`B_P` = 站点内 `P_first_basis='IMPORTED'` 的最小 `P_first_date`，走 D1 的部分索引。命中「首次进入当前榜单」：日视图 `d`：`day_first_basis='IMPORTED' AND day_first_date = d AND d > B_DAY`；周视图 `w`：`week_first_basis='IMPORTED' AND week_first_date = w AND w > B_WEEK`。**不做日周映射**：日榜首次只看日报历史，周榜首次只看周报历史。`d ≤ B_P` 不可判断，与共享口径的处理一致。

新站点先导周报再导日报时，日报第一期的词全是 ABSENT→IMPORTED 且日期 = `B_DAY`，不算首次；第二期起才有日榜首次。与共享口径的基线规则同构。

### D4 缺期提示

查询该期是否有首次结果，不改变命中；只在数量接口增加 `previous_period_missing`：所选期次之前、同站点同周期的上一期（日 `d-1`，周 `w-7`）没有 COMPLETED 批次且该期晚于 `B_P` 时为真。读 `import_batch`（不参与保留清理，`idx_import_batch_site_period` 覆盖）。界面提示「上一期缺失，部分词可能更早已出现」。只在 `PERIOD` 口径给出，`MONITOR` 口径行为不变。

### D5 API

- 请求新增 `first_listing: Literal["PERIOD","MONITOR"] | None`。`first_listed=true` 视为 `MONITOR`；`first_listed=true` 且 `first_listing="PERIOD"` → 422（模型校验，不访问数据库，导出因此在开流前失败）。规范化后服务内部只认一个有效口径 `effective_first_listing(req)`，`request_has_filters`、`result_key`、筛选、计数、导出、摘要日志都用它，避免旧/新两个字段被分别解释。
- `result_key`：新字段登记为枚举；`first_listed=true` 与 `first_listing=MONITOR` 规范成同一个键（同一结果）；不同口径不同键。
- 行新增 `period_first_listed: bool`、`period_first_date`、`period_first_basis`（当前视图对应周期）；`first_listed` / `first_seen_*` 原样。掉榜行恒 false。
- `GET /api/analysis/first-listed` 增加 `mode=PERIOD|MONITOR`（缺省 MONITOR，旧客户端不变），响应增加 `mode` 与 `previous_period_missing`。
- 导出：保留「首次入榜」列（共享判定，列名与含义不变，下游表格不破），在其后新增「首次进入日榜」（日视图）或「首次进入周榜」（周视图）列取周期判定；两列不随所选口径增减，口径由列名写明。
- 品牌排除：界面在两种首次口径下都随默认发送（现为仅 `first_listed`）。

### D6 前端

- 筛选控件「首次」：`全部 / 首次进入日榜（周视图文案为周榜）/ 首次进入监测`，实心按钮组（#177 约定，禁用 Segmented）。草稿字段 `firstListing?: 'PERIOD' | 'MONITOR'`；旧快照只有 `firstListed: true` 时读为 `MONITOR`。**加字段不提升 `SAVED_MODE_VERSION`**（savedMode 已有「新增可选字段向后兼容」的约定），内置默认仍是 `MONITOR`。
- 视图切换不改草稿；`PERIOD` 的文案随视图变成日榜/周榜。
- 现排名标签：`PERIOD` 口径显示「首次上日榜/首次上周榜」（用 `period_first_listed`），其余口径显示「首次进入监测」（用 `first_listed`）。数量标题与说明随口径。
- 趋势详情：列出日榜首次、周榜首次、共享监测首次三项，各带日期与等级；`EARLIEST_OBSERVED` 写「最早可观测（此前可能已出现）」，`UNKNOWN` 写「未知」，`ABSENT` 写「未上过该榜」。详情接口复用维度行，不新增查询路径。

### D7 回填 `scripts/backfill_period_first.py`

集合式而非逐期推进：对每个站点、每个周期 `P`，从事实表取每个词的 `min(期次)` 与该周期最早期次 `B0_P`，一条 UPDATE 写入目标值：

- 有事实：日期 = min；等级 = `IMPORTED` 若 min > `B0_P`，否则 `EARLIEST_OBSERVED`。
- 无事实（只在另一周期或已不在库）：(NULL, ABSENT)——**仅在证据完整时**；这是「确知未出现」，证据不完整就不能写。

与逐期导入等价的前提就是证据完整：事实表含该周期全部已导入期次，min 即逐期导入时第一次见到的期次；`B0_P` 当期记最早可观测而非导入创建，与 #143 重放的取舍一致（`B0_P` 当期本来就不可判断）。证据完整性校验复用 `backfill_first_seen.evidence_gaps`（最早期次 = 最早已完成批次、每个已完成批次事实都在、无清理分区），任一周期不通过则该站点整体拒绝，不降级。

- 两个周期合成一条 UPDATE（每行至多改写一次），`IS DISTINCT FROM` 守卫，第二次运行零更新。
- 写前把被改动行的原四列存入普通表 `analysis.bak_period_first_<时间>`，`--undo` 只恢复仍等于回填写入值的行（与 #143 脚本同一约定）。
- `--dry-run` 真跑后回滚，输出各站点各周期各等级计数、`B_P`、抽样核验（抽 N 个 IMPORTED 词，比对事实表最早期次）。
- 持 `IMPORT_LOCK_KEY` 咨询锁，拿不到即退出。
- **共享记录不动。**
- 发布后、回填前到达的导入：存量词 UNKNOWN→EARLIEST_OBSERVED，新词正常 IMPORTED/ABSENT；回填按事实重算，覆盖前者为正确值，后者结果相同。因此「发布与回填之间有导入」不影响结果。

## 性能与资源

- **导入**：每次导入多一条对 staging 去重后的 UPDATE（存量词首次出现在该周期的少量行 + 早于现值的乱序行），新词 INSERT 多写两列。正常顺序导入时，被改动行数约等于当期新出现在该周期的词数（日报约千～万级）。仍在同一事务、同一连接，不增加连接池占用；内存无变化（SQL 内完成）。
- **查询**：周期命中是 `dim_keyword` 上的等值条件，配合部分索引；`B_P` 用部分索引最左项 `min()`。`PERIOD` 计数沿用 `_count_base_query`，只换判定表达式。实施时在本地真实规模数据上对日视图 US 与周视图 US 各跑一次 `EXPLAIN (ANALYZE, BUFFERS)`，确认 `B_P` 走索引、命中不全表扫描 dim_keyword。
- **回填**：每站点每周期一次 `GROUP BY keyword_id` 扫事实表（US 周报 9 期约 2600 万行，日报 56 期约 2400 万行），在锁内串行；一次 UPDATE 约 620 万行，`dim_keyword` 约翻倍膨胀，之后 `VACUUM (ANALYZE) analysis.dim_keyword`（写操作，单独确认）。生产单机 4C8G，选业务低峰执行。

## Risks / Trade-offs

- **12-01 截止**：发布或回填晚于 9 月分区清理，`evidence_gaps` 会拒绝，周期证据只能停留在 UNKNOWN/EARLIEST_OBSERVED（不可确认）。缓解：截止前完成；否则先把 `OPS_RETENTION_MODE` 改为 `dry_run`（用户决定）。
- **ABSENT 的正确性依赖证据完整**：若某周期曾有已导入但被清理的期次，ABSENT 会把见过的词误判成未见。回填只在校验通过时写 ABSENT；导入只在新建行时写 ABSENT（新建即证明全站点从未见过）。
- **两个“首次”并存的理解成本**：用三选一控件、口径随行标签与数量标题、详情里三项证据并列解释。
- **dim_keyword 改写膨胀**：一次性，VACUUM 收回。

## Migration Plan

1. 合并后由 `Deploy V2 to ECS` 发布（迁移随部署执行）——发布需用户当次明确授权。
2. 生产 `--dry-run` 预览，核对三站各周期等级计数、`B_P` 与 #178 四个样例。
3. 用户确认后正式回填，`VACUUM (ANALYZE)`。
4. 浏览器验收 10/31 日视图「首次进入日榜」。
5. 回滚：`--undo bak_period_first_<时间>`；代码回滚时新列保留无害（旧代码不读），downgrade 只在确认不再需要时执行。

## Open Questions

无阻塞项。12-01 前能否完成发布与回填由排期决定，见 Risks。
