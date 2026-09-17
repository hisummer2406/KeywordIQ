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
同一个事务里完成）的少量场景，用 `connection.begin_nested()`（SAVEPOINT）包一层：
`run_retention` 内部的 `session.commit()` 只会释放这个 SAVEPOINT，外层事务仍然存活，
测试结束时外层 `rollback()` 照常把一切清空，不会真正污染 `keywordiq_test`。每次调用
`run_retention`（非 dry-run）前都要重新开一个 SAVEPOINT——`commit()` 释放的是"当前"
那一层，同一个 SAVEPOINT 不能跨两次调用重复使用。

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
