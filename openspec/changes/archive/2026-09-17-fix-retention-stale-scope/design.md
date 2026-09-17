## 决策 1：修根源（清理残留统计），不在查询侧叠加日期过滤

`available_dates_stmt`（`analysis_crud.py`/`growth_crud.py`）与 `_resolve_date`
（`analysis_service.py`/`growth_service.py`/`library_service.py`）已经是正确的契约：
"日期不在可选集内 → 拒绝"。问题不在这条契约，在于可选集的来源
（`site_data_stats.report_date`）本身混入了物理上已经不存在的日期。

两种修法都能让 `available_dates` 不再包含残留日期：
（A）给 `available_dates_stmt` 加一条 `report_date >= cutoff`（`cutoff` 现算，基于
`date.today()`）；（B）让保留清理真正删掉这些残留行。

选 (B)，不选 (A)，原因是 (A) 会引入一个新的、更早的边界：`cutoff` 一旦跨入新的月份
（日历翻页），即使这一次保留清理还没运行（分区还在、数据物理上仍然存在），(A) 会立刻
在查询层把那个月的数据判定为"不可选"——这是把"清理调度尚未执行"误判成"数据已不存在"，
让本来正确的数据凭空不可查一段时间（直到下次清理运行前的这段时间窗口）。(B) 不会有这个
问题：残留行只在清理**真正执行**、分区**真正被移出**之后才消失，可选集与物理数据始终
同步。

`trend_service.py` 里已有一段专门为"残留统计"写的防御代码
（`_floor_for_range` 的 `d >= cutoff` 过滤、`imported_dates` 集合），并配有回归测试
（`test_floor_for_range_all_ignores_stale_dates_before_retention_cutoff`、
`test_floor_for_range_all_never_exceeds_anchor_when_anchor_itself_is_stale`）。这些测试
传入的是手工构造的 Python list，不经过真实数据库，因此不受本变更影响，继续通过——
它们验证的是"就算调用方给的 available_dates 里混进了残留日期，这段计算也不会因此错"，
属于双重保险，本变更不删除、不需要修改。

## 决策 2：残留统计的清理无条件执行，不依赖"本次是否 DROP 了分区"

复现步骤里，分区已经被 DROP，但如果本变更只在"这次确实移出了某个月度分区"的分支里去
清理对应的统计行，就治不好历史遗留——本变更上线前，生产库可能已经有多个月份"分区早被
移出、统计行却还在"（`CLAUDE.md` 明确写了这是"既有代码一个尚未触发问题的缺口"）。所以
清理逻辑按 `cutoff` 独立判定："`site_data_stats`/`category_stats`/`brand_stats` 里
`report_date < cutoff` 的行都清"，不管这次分区遍历里有没有实际 DROP 什么。这样存量残留
在本变更上线后的第一次保留清理运行时就会被处理掉，不需要额外补一个一次性脚本。

## 决策 3：`category_stats`/`brand_stats` 没有 ORM 模型，清理用与 `filter_stats.py`
同风格的 `text()` 原生 SQL

这两张表是 `f5a6b7c8d9e0` 迁移引入的按 (站点,周期,日期,id) 分片的普通表，主键前缀正是
`(site, period_type, report_date)`，删除是主键范围扫描，代价与残留月份数同阶、不随
数据总量增长。`site_data_stats` 有 ORM 模型（`SiteDataStats`），但为了让三张表的清理
逻辑保持同一种写法（同一个函数、同一段 SQL 模板），三张表统一走 `text()`，不为
`site_data_stats` 单独引入 ORM `delete()` 语句。

## 决策 4：`scripts/prune_retention.py` 的早退判断必须同时看残留统计

脚本原有逻辑是"这次没有分区要移出就直接退出，不问是否要执行"
（`if preview.total_dropped == 0: return 0`）。决策 2 意味着残留统计的清理与"这次是否
移出分区"是两件独立的事——一个站点可能这次没有分区超期，但历史残留的统计行还没清过。
早退判断改为同时看两者：`total_dropped == 0 and total_purged_stats == 0` 才真正退出，
否则即使这次没有分区可移出，也要把残留统计清掉。

## 决策 5：测试 `run_retention` 本身（会真的 `commit()`）不能直接用共享测试库的
"外层事务回滚"隔离

`test_retention_integration.py` 现有的 `conn` fixture 把整个测试包在一个事务里，结束时
`trans.rollback()`，前提是被测代码**不主动 commit**——现有测试确实只调用不提交的
`partitioning.*` 函数，绕开了 `run_retention` 唯一的 `session.commit()`。

本变更要新增覆盖"清理残留统计"的逻辑本身也拆成一个不提交的函数（与
`partitioning.drop_month_partition` 同一层次的约定：DDL/DML 函数不提交，提交权在
service 层），沿用现有 `conn` fixture 直接调用，覆盖大部分场景（正常清理、历史残留、
dry-run、幂等）。

对于需要验证 `run_retention` orchestration 本身（分区 DROP 与统计清理在同一次调用、
同一个事务里完成）的场景，最初想用 `Session(bind=conn,
join_transaction_mode="create_savepoint")` 包一层外层 `conn` fixture 的连接，让内部
`commit()` 只释放 SAVEPOINT——但决策 7 引入的咨询锁（`import_service.import_lock`）
需要 `session.get_bind()` 是一个真正的 `Engine`（它要在上面独立开一条**新**连接，
不能借用 session 自己的），而 `Session(bind=conn, ...)` 的 `get_bind()` 返回的是
`Connection`，没有 `.connect()`，会直接 `AttributeError`。

因此改为两类测试分别处理：

- **锁被别人占着 → 直接跳过、什么都不写**：这条路径 `run_retention` 内部不会走到
  `_enumerate_and_mutate()`，也不会 `commit()`，可以放心用一个真实的 `Engine` 绑定
  会话——不会有任何提交，不需要清理，也不会污染共享库。
- **拿到锁 → 真实端到端跑一遍**：这条必须真提交，改用真实的 `Engine` 绑定会话，
  `run_retention` 自己负责清理它移出的那部分（超期分区/事实行/超期统计——这正是
  被测的行为本身），测试只需在 `finally` 里手工清理 `run_retention`**不会**碰的
  两样：`dim_keyword` 维度行（不受保留清理管理）、窗口内的对照组
  `site_data_stats`（用来证明不受影响，真实数据不该被这次清理动，所以也不会被
  `run_retention` 自动清走）。

## 决策 6：保留清理的写入路径必须持有与导入共用的咨询锁（PR #101 复审 P1）

复审指出一条本变更没堵住的路径：导入的事实写入与 `site_data_stats` 回写是两次**独立
提交**（`import_service._execute_locked` 先 `import_report()` + commit，再
`_update_site_data_stats()` + commit）。如果保留清理恰好在这两次提交之间跑完"移出
分区 + 清理统计 + 提交"，会先把导入刚写完事实行的分区 DROP 掉（此时清理不到任何统计
残留，因为导入还没提交那一步），随后导入照常提交一条全新的 `site_data_stats` 行——
事实已经不存在，统计却说有，正是本变更要消灭的 `total>0, data=[]`，只是换了个触发
路径。这条路径本变更之前就存在（保留清理一直没有跟导入协调过），不是本次改动引入的
新问题，但既然本 PR 的目标就是"让这个状态不再可能出现"，留一条已知能触发它的路径不修
说不过去。

修法是让保留清理的非 dry-run 路径也持有 `import_service.IMPORT_LOCK_KEY`（原来是
`import_service` 内部私有的 `_import_lock`/`_IMPORT_LOCK_KEY`，现改为公开的
`import_lock`/`IMPORT_LOCK_KEY`——这不再是导入模块的实现细节，是导入与保留清理之间
唯一的协调点，两侧都要能看到同一个符号）。拿不到锁（另一个导入正在进行）就什么都不做、
直接返回 `lock_skipped=True`，不阻塞、不重试——保留清理本来就是按调度周期性触发，下一
轮自然会再试，没有必要在这里等。

**没有重构导入管线**：`import_service.py` 除了把两个私有符号改名之外没有任何行为变化，
两个既有调用点（`run_import`/`drain_import_queue`）原样不变。这与 issue #100 的非目标
"不重构导入管线"不冲突——只是让保留清理去遵守一把已经存在的锁，不是给导入加新东西。

**为什么不在读枚举阶段之外单独收窄锁的范围**：分区是否超期只取决于 `cutoff` 与分区
边界，与"导入是否正在写入"无关；真正需要互斥的是"移出分区+清理统计+提交"这整段——
只要这段与导入的整个 `_execute_locked`（同样整段持锁）互斥，就不存在两者交错的窗口。
枚举阶段单独拆出去意义不大，反而多一次锁的获取/释放，所以枚举也放进锁里一起做。

## 决策 7：`category_stats`/`brand_stats` 补一个 `report_date` 单列索引（PR #101 复审 P2）

`purge_stale_stats` 只按 `report_date` 过滤，不带 `site`/`period_type` 等值条件。这两张
表的唯一索引是主键 `(site, period_type, report_date, id)`——`report_date` 排第三位，
缺前两列的等值条件时用不上这个前缀，退化成全表扫描。`site_data_stats` 行数小（每
(站点,周期,日期) 一行）可以不管；但 `category_stats`/`brand_stats` 按
(站点,周期,日期,id) 分片，`filter_stats.py` 记录的品牌基数约 15.97 万/站/天，三个月
窗口、多站点叠起来是千万级行——原设计文档字符串里"代价与残留月份数同阶、不随数据总量
增长"这句话对这两张表是错的，已经改正并说明依赖这个新索引。

新迁移 `47df76b2f424` 只加两个单列索引（`report_date`），不改表结构，不影响主键覆盖
的其他查询路径（按站点/周期/日期取切片仍然走主键）。选单列索引而不是把 `site`/
`period_type` 也塞进去，是因为这条清理路径的过滤条件本来就只有 `report_date`——加多余
列只会让索引更大、维护成本更高，换不来任何这条查询用得上的东西。用 `EXPLAIN` 核对过
新库上确实走 `Index Only Scan`。

## 已核查、判定不受影响的路径

- **`growth_crud.available_dates_stmt`**：与 `analysis_crud` 同一张 `site_data_stats`
  同一个缺陷（docstring 同样声称"在保留窗口内"却没有过滤），本变更修好底层统计后
  这条自动恢复正确，不需要单独改代码。
- **`growth_crud.count_stmt`/`list_stmt`**：直接查 `fact_growth`（保留清理会移出它的
  分区），不经过任何预存统计，选中一个已被移出的锚定日只会诚实地查出 0 行——不存在
  "总数非零但结果为空"的矛盾，本身自洽，不属于本 issue 的缺陷范围。
- **`library_service`**：`_available_dates` 复用同一个
  `analysis_crud.available_dates_stmt`，随底层修复自动获益；分组条目（
  `keyword_group_item`）与事实数据的生命周期无关，清理只影响"该期是否有数据"的判定，
  不会删除条目本身。
