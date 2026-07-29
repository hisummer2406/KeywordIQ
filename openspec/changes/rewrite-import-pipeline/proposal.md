## Why

V2 的数据模型（星型 + 分区 + 不可变事实）已落地并归档，但**没有任何代码往里写数据**。V1 的导入链路（`csv_processor.py` 的两条 UPSERT + `upload_service.py`）是为旧宽表写的，与新模型完全不兼容：它做 `ON CONFLICT (keyword)` 合并、写 `ranking_trend_day` JSONB、日报周报共用一行——这些在 V2 里全部不成立。

本变更把导入链路按 V2 模型重写。这是 V2 能真正跑起来的第一块：没有它，四张新表永远是空的。

本变更**同时影响日报路径与周报路径**，且两者被刻意做成**同一套三段式流程**（只是目标事实表与"上一期"定义不同）——这是分表 + 时序化带来的简化，V1 那种两条各 90 行、彼此漂移的 UPSERT SQL 不复存在。

## What Changes

- **导入改为三段式**，取代 V1 的逐行 UPSERT：
  1. `COPY` 原始 CSV → `UNLOGGED` 无索引暂存表（流式，不整文件入内存）
  2. 从暂存表补齐 `dim_keyword` 与 `dim_product`（维度先于事实，不产生重复维度）
  3. 关联上一期（前一天/前一周）算出 `previous_ranking`/`ranking_change`/`is_new`，**追加**写入目标分区
- **写入前调用地基提供的 `ensure_fact_partitions`**——分区缺失会直接失败，这一步不可省。
- **删除 V1 导入代码**：两条 UPSERT SQL、`_fetch_existing_trends`、`ranking_trend_day` 相关逻辑全部移除。
- **站点参数必填 + 文件名前缀一致性校验**——缺站点或前缀冲突时在文件落盘前拒绝。
- **同一期重复导入 = 显式覆盖**：先移除该 `(site, period, report_date)` 切片再写入。
- **导入串行执行**：用 `pg_try_advisory_lock` 保证同一时刻只有一个导入在跑，拿不到锁的置为 `QUEUED` 排队（2C4G 上两个 GB 级导入并发会互相争抢 WAL 与索引构建）。
- **导入后更新 `site_data_stats` 并 `REFRESH MATERIALIZED VIEW CONCURRENTLY` 刷新类目物化视图**（service 层控制事务边界）。
- **原始文件归档钩子**：导入成功后把原始 CSV 移到本地归档目录（可配置路径）。**只留钩子，暂不接 OSS**——但从第一天起就保留原始文件，避免历史报表永久丢失（对将来变更 5 的 OSS 归档铺路）。
- **触发方式沿用 V1**：HTTP 上传文件 → 后台任务异步处理。前端交互不变,只换处理内核。
- **修正配置漂移**：`BATCH_SIZE`/`MINIBATCH_SIZE` 校正回安全值；`ImportBatch` 用新模型（含 site/period/report_date）。

**前置已完成**：`database.py` 的 TCP keepalive 已补（治导入期卡死风险），本变更据此可安全执行长时导入。

## Capabilities

### New Capabilities
（无新增。）

### Modified Capabilities
- `site-scoped-import`: 本变更实现该能力。规格草稿已在 specs/ 就位（源自数据模型变更的推迟规格），本变更据其实现，并补充原始文件归档、串行执行、导入后刷新等要求。

## Impact

**代码**
- `app/table/upload/csv_processor.py`：重写——删两条 UPSERT，改为三段式（COPY/维度/事实）。
- `app/table/upload/upload_service.py`：重写编排逻辑——站点校验、分区确保、咨询锁、覆盖语义、导入后刷新、原始文件归档。
- `app/table/upload/upload_api.py`：站点参数必填、文件名校验；触发形态（上传+后台）保留。
- `app/table/upload/upload_schemas.py`：请求 DTO 增加站点、周期。
- 引用 `app.site_registry`、`app.table.analysis.partitioning`、`ImportBatch`、`SiteDataStats`（均由数据模型变更提供）。
- `.env` / `.env.example`：归档目录配置、批处理参数校正。

**运行时**
- 导入从逐行 UPSERT 改为批量 COPY + 集合式关联，死元组归零，预期耗时显著下降（需实测）。
- 串行执行保证导入侧连接占用不随站点数增长。

**不在本变更范围**
- 查询链路（变更 3）、界面（变更 4）、OSS 归档管道（变更 5）。
- V1 旧表的删除（数据模型变更的上线任务）。
