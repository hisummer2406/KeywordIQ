## Why

市场分析页的「新词」目前定义为「本期之前 7 天内没出现过」（`is_new`，导入时写进事实行）。它只看滚动窗口，所以一个词掉榜 8 天再回来又会被当成新词；日报与周报各算各的；窗口外的历史被保留清理后无从判断。#137 要的是另一件事：**关键词第一次进入监测范围**，永久、日周共享、不因掉榜/清理而重置。

## What Changes

- `dim_keyword` 新增永久首次入榜记录：`first_seen_date`、`first_seen_period_type`（DAY/WEEK）、`first_seen_basis`（`CONFIRMED` 已确认 / `EARLIEST_OBSERVED` 最早可观测 / `UNKNOWN` 未知）。`dim_keyword` 不分区、不参与保留清理，天然是“见过的词”的永久集合。
- 导入第二段（补齐维度）同时写首次记录：新词条带本次期次入库；已存在的词只在证据更早时向前修正（`(日期, 日优先于周)` 取小），重复导入、重试、乱序、日周交叉导入结果一致。
- 站点最早一期（及更早的补历史）里首次遇到的词只记 `EARLIEST_OBSERVED`，**不算首次入榜**——避免新站点首导、补更早报告时整批涌进当期。
- 存量迁移：迁移只加列，既有词一律 `UNKNOWN`；`scripts/backfill_first_seen.py` 用库内仍保留的事实数据把能证明的最早日期补成 `EARLIEST_OBSERVED`，**永不写成 `CONFIRMED`**，输出处理数/无证据数/抽样核验。
- 查询侧：「新词」筛选、列、导出改为「首次入榜」，判据是 `dim_keyword` 首次记录落在所选期次内（日视图按日；周视图按周期窗口 `[w-6, w]`，周报来源只有周精度，不捏造某一天）。当期「首次入榜」数量查询时按同一口径实算，不再存按期统计，因此补历史/乱序不需要统计校正。
- 退役 `fact_daily/fact_weekly.is_new` 的 7 天口径：停止写入、迁移删列，`scripts/backfill_is_new.py` 删除。
- **BREAKING**（对内）：请求字段 `is_new` → `first_listed`；保存的条件版本号 +1，旧保存条件静默丢弃（沿用既有约定）。

## Capabilities

### New Capabilities
- `keyword-first-seen`: 关键词永久首次入榜记录的口径、写入规则、日周映射、存量基线与回填边界。

### Modified Capabilities
- `site-scoped-import`: 导入补齐维度时写首次记录；不再在事实行写 `is_new`。
- `site-scoped-query`: 「新词」筛选/排序/导出改为首次入榜口径，并提供当期首次入榜数量与统计范围说明。
- `analysis-screen-layout`: 市场分析页「新词」改名「首次入榜」，展示当期数量、说明文案与「统计范围不完整」提示。
- `keyword-library`: 盯盘行的「是否新词」列改为「是否首次入榜」，仍与分析屏共用同一份列投影。
- `multi-site-data-model`: `dim_keyword` 增加首次入榜列与索引；事实表删除 `is_new`。

## Impact

- 后端：`app/table/analysis/dimension_model.py`、`fact_model.py`、`analysis_crud.py`/`analysis_service.py`、`app/table/upload/importer.py`、新 alembic 迁移（加列、索引、删列）、`scripts/backfill_first_seen.py`（新）、`scripts/backfill_is_new.py`（删）。
- 前端：`pages/Analysis/`（`filters.ts`、`FilterPanel.tsx`、`columns.tsx`、`index.tsx`、`savedMode.ts`）。
- 文档：`CLAUDE.md` 新词相关一节需重写；关联 #119 的口径条目同步。
- 不在范围：增长市场（见 #138）、#119 中数量对照/7 天变化率/类目过滤等其余事项。
