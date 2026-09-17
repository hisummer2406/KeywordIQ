## Why

issue #100：保留清理（`retention_service.run_retention`）DROP 超期月度分区时，从未清理
`site_data_stats` / `category_stats` / `brand_stats` 里对应的预存统计行。这三张表按
(站点, 周期, 日期) 分片存量，是日期选择器、无筛选总数、类目/品牌下拉的唯一来源——分区被
物理移出后，这些残留行让查询侧继续相信"这个日期还能查"，实测复现为
`get_scope` 的日期选择器仍列出已清理日期，`search` 对该日期返回
`success=true, total=1, data=[]`（总数非零、结果为空的矛盾响应）。

`site-scoped-query` spec 里"日期选择器只提供已导入且在保留窗口内的日期"这条要求本就
写着"已归档移出的日期 MUST NOT 可选"——这是实现没有满足既有 spec 的缺陷，不是需要
重新决策的新行为。

## What Changes

- `retention_service.run_retention` 在移出超期分区的同一事务内，无条件（不依赖"本次
  是否真的 DROP 了分区"）清理 `site_data_stats`/`category_stats`/`brand_stats` 中
  `report_date < cutoff` 的残留行——覆盖本次清理与历史上已经产生的残留。
- `dry_run` 下只统计将被清理的行数，不写入；重复运行是幂等的（第二次清理不到任何行）。
- `scripts/prune_retention.py` 的"没有要移出的分区就直接退出"判断补上残留统计的计数，
  否则历史残留（分区早已在本变更之前被移出、只剩统计行）永远等不到清理的机会。
- 不改变 `available_dates_stmt`（`analysis_crud`/`growth_crud`）与 `_resolve_date` 系列
  函数本身——它们的既有契约（"未在可选集内即拒绝"）本就正确，一旦底层统计不再残留，
  这条契约自动生效，不需要在查询侧叠加一层日期过滤。
- 核查（非修改）关联路径：`trend_service` 已有的"不信任残留统计"防御性判断
  （`d < cutoff`、`imported_dates` 过滤）保持不变，作为双重保险；`growth_crud` 的榜单
  计数直接查 `fact_growth`（保留清理会移出该表分区），本就自洽，不受这个缺陷影响；
  `library_service` 复用同一个 `available_dates_stmt`，条目本身不随事实清理消失。

## Capabilities

### Modified Capabilities
- `multi-site-data-model`：保留清理的移出范围从"两张事实表 + 掉榜表的月度分区"扩展到
  "同时清理对应的预存统计残留"，是同一个"保留清理"能力的既有职责补全，不是新能力。

## Impact

- 代码：`app/table/analysis/retention_service.py`（新增清理逻辑）、
  `scripts/prune_retention.py`（早退判断补上残留统计计数、日志补充）。
- 不改动：事实表结构、分区方案、保留月数、`site-scoped-query`/`growth-market-list`/
  `keyword-library`/`keyword-trend-detail` 的既有 spec 与查询代码。
- 测试：`test/test_retention_integration.py` 新增真实 PostgreSQL 集成用例，覆盖复现
  场景、窗口边界、历史残留（无匹配分区）、dry-run 不写入、幂等重跑。

Refs #100
