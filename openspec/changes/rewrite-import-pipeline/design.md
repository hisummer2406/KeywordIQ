## Context

V2 数据模型已归档并落地：`dim_keyword` / `dim_product` / `fact_daily` / `fact_weekly`（分区）/ `site_data_stats` / `import_batch` / `sites` / `category_stats`（物化视图），以及 `app.table.analysis.partitioning.ensure_fact_partitions` 和 `app.site_registry`。本变更为这些空表写入导入逻辑。

V1 导入链路与新模型不兼容，必须重写而非改造：
- `csv_processor.py` 的两条 `ON CONFLICT (keyword)` UPSERT SQL —— 冲突键、写宽表、写 `ranking_trend_day` JSONB，全部作废。
- `_fetch_existing_trends` —— 时序化后趋势从历史行算，该函数连同 JSONB 字段一起删。
- 日报周报共用一行的合并逻辑 —— 分表后消失。

前置已完成：`database.py` 同步引擎已补 TCP keepalive，长时导入的连接不再有半开卡死风险（`statement_timeout` 未全局设，正因为它会杀掉长导入）。

## Goals / Non-Goals

**Goals:**
- 把日报与周报导入统一为同一套三段式流程（COPY → 维度 → 事实追加）。
- 纯追加写入，无 UPSERT、无死元组。
- 导入前确保分区存在；同一期重复导入为原子覆盖。
- 导入串行化，连接占用不随站点数增长。
- 导入后刷新统计与类目物化视图。
- 原始文件保留归档（本地钩子，为 OSS 铺路）。

**Non-Goals:**
- 查询链路（变更 3）、界面（变更 4）、OSS 归档管道（变更 5）。
- 改动触发形态——沿用 V1 的 HTTP 上传 + 后台任务。
- 跨站点/跨周期的任何合并。

## Decisions

### 决策 1：三段式导入，日报周报共用同一套代码

```
第一段 COPY   原始 CSV → analysis 下的 UNLOGGED 无索引暂存表（每次导入临时建、用完 DROP）
第二段 维度   从暂存表 INSERT ... ON CONFLICT DO NOTHING 补齐 dim_keyword / dim_product
第三段 事实   暂存表 JOIN 维度 + 关联上一期算派生值 → 追加写目标分区
```

日报与周报的差异仅两处：目标事实表（`fact_daily` vs `fact_weekly`）、"上一期"的定义（前一天 vs 前一周）。其余完全一致。这是分表 + 时序化换来的最大简化——用一个 `period` 参数驱动同一份代码，取代 V1 两条各约 90 行、彼此漂移的 UPSERT。

**为什么 COPY 而非逐行 INSERT**：GB 级周报逐行绑定参数是 V1 慢的主因之一。`COPY` 是 PostgreSQL 最快的批量装载路径，且流式、不整文件入内存。

**为什么暂存表 UNLOGGED**：暂存数据不需要崩溃恢复，`UNLOGGED` 免去 WAL 写入，装载更快。用完即 DROP，不留痕迹。

### 决策 2：派生值用集合式 SQL 一次算出，不逐行

`previous_ranking` / `ranking_change` / `is_new` 通过暂存表 `LEFT JOIN 上一期分区`（按 `(site, keyword_id)`）一次性算出：

- 上一期存在 → `previous = 上期 current`，`change = 本期 current - 上期 current`，`is_new = false`
- 上一期不存在 → `previous = 0`，`change = 0`，`is_new = true`

"上一期"只取**同站点同周期**的数据（规格硬约束：派生数据不得跨站点或跨周期继承）。日报取前一天分区，周报取前一周分区。

### 决策 3：同一期重复导入 = 事务内先删后插

覆盖语义用 `DELETE FROM fact_x WHERE site=? AND report_date=?` 再插入，整体在一个事务里，保证外部观察不到"删了一半"的中间态。这会产生死元组，但重复导入是罕见的纠错场景，可接受（不破坏"常态纯追加"的性质）。

### 决策 4：串行化用 PostgreSQL 咨询锁

导入前 `SELECT pg_try_advisory_lock(<固定key>)`。拿到才执行；拿不到把批次置 `QUEUED` 并返回提示，不并行。锁在导入结束（成功或失败）释放。

**`QUEUED` 必须有接手者**（补记，源于线上现象）：本决策最初只写到「置 `QUEUED` 并返回提示」，实现也照做了——而没有任何东西会回头看这些批次。日报要跑约 4 分钟，这期间提交的第二个文件就此永久停在 `QUEUED`，界面显示「队列中」，用户在等一件不会发生的事。接手者是 `app/table/upload/import_queue.py` 的轮询任务（间隔 `IMPORT_QUEUE_POLL_SECONDS`），多 worker 各起一个、仍由同一把咨询锁保证串行。

用轮询而非「导入结束时接着跑下一个」：链式接力在进程被杀、导入崩在半路、或前一个导入由别的 worker 执行时都会断链，断链的表现正是「永远排队」。

重建不出数据源的批次（本地退路无 object_key）不进队列，当场判 `FAILED` 并写明需重新上传。

**为什么用咨询锁而非应用层信号量**：应用是多 worker（`uvicorn --workers 4`）+ 可能多进程导入，进程内信号量拦不住跨进程并发。咨询锁在数据库层，天然跨进程有效。

**连接池影响**：串行保证任一时刻只有一个导入在占用导入连接，`(DB_POOL_SIZE + DB_MAX_OVERFLOW)` 预算不被多站点稀释。

### 决策 5：分区确保 + 归档 = service 层职责

- 写事实表前调 `ensure_fact_partitions(session, table, site, report_date)`（地基已提供，幂等）。跨月首次导入自动建分区。
- 导入成功后把原始文件移到 `IMPORT_ARCHIVE_DIR`（新增配置）。**归档目标做成一个可替换的接口**（`archive_raw_file(path) -> location`），当前实现是本地移动，将来换成 OSS 上传只改这一个实现，不动导入主流程。

### 决策 6：导入后刷新在事务边界外

`site_data_stats` 更新与 `REFRESH MATERIALIZED VIEW CONCURRENTLY category_stats` 放在事实数据提交**之后**单独执行。理由：`REFRESH CONCURRENTLY` 不能在已有事务块内运行，且刷新失败不应回滚已落库的事实数据（数据是对的，只是选项/统计稍旧，下次导入会再刷）。

### 决策 7：多进程分片保留，但每分片自带站点/日期

大文件仍走 V1 的分片 + `ProcessPoolExecutor` 模式（`NullPool` 子进程自建引擎已正确）。改动：每个分片任务额外携带 `site` 与 `report_date`，写入时带上，保证分片间归属一致（规格要求）。暂存表方案下，也可以让各分片 COPY 进各自的暂存分片再汇总——具体分片粒度在实现时按实测定。

## Risks / Trade-offs

- **[COPY 的类型/编码解析比 pandas 宽松度低]** → V1 用 pandas 读 CSV 容错性强；`COPY` 对格式更严格。需要在 COPY 前做一遍最小清洗（列数、编码、空值），或用 `COPY ... CSV` 的容错选项。上线前用真实报表样本验证解析。

- **[维度补齐的并发写入]** → 串行导入下 `dim_keyword`/`dim_product` 不会并发写，`ON CONFLICT DO NOTHING` 足够。若将来放开并行，需重新评估。

- **[覆盖语义的死元组]** → 仅重复导入时产生，罕见。若某站点频繁重导，可对该分区手动 VACUUM。

- **[REFRESH CONCURRENTLY 的成本]** → 物化视图刷新在大数据量下有成本，但每次导入才刷一次，频率低，可接受。需确认唯一索引存在（地基已建）。

- **[原始文件归档占本地磁盘]** → 本地归档会累积占盘。需要一个清理/转储策略（或尽快接 OSS）。当前只留钩子，配置项里标注这一点。

## Migration Plan

本变更不改数据库结构（表由数据模型变更提供），因此**无 Alembic 迁移**。唯一的 DDL 是导入时临时建/删 UNLOGGED 暂存表，属运行时行为，不进迁移。

部署顺序：
1. 校正 `.env` 的 `BATCH_SIZE`/`MINIBATCH_SIZE`，新增 `IMPORT_ARCHIVE_DIR`。
2. 部署新导入代码。
3. 用小样本 CSV 验证三段式全流程（COPY→维度→事实、覆盖、串行、刷新、归档）。
4. 逐站点导入真实数据。

回滚：本变更纯代码，回滚 = 部署上一版本。已写入的事实数据不受影响（结构未变）。

## V1 清洗逻辑盘点结论（任务 1.1，已完成）

对照 `csv_processor.py` + `upload_service.py` 逐条判定，COPY 方案的取舍：

| V1 清洗 | 位置 | V2 处置 |
|---|---|---|
| 三段式结构校验（`报告范围`/`搜索频率排名`/`搜索词` 关键字） | `validate_csv_structure` | **必须保留**：COPY 前判合法性，`skiprows=2` 依赖它 |
| **列按位置映射（忽略表头文字）**，固定 21 列顺序 | `_create_column_mapping` | **必须保留且是 COPY 命脉**：COPY 也按位置装载，天然契合。风险：亚马逊改列序会静默错位 |
| 数值列 `to_numeric(errors='coerce').fillna(0)`（脏数字转 0 不报错） | `_clean_chunk_data` | **必须保留**：改在暂存表→事实表用 SQL 做（脏值转 0）。否则真实报表任一脏数字会让 COPY 整批失败 |
| 排名 `int(float(x))`（容忍 "10.0"） | `_process_mini_batch` | **必须保留**：暂存 text 列 `::numeric::int` |
| 字符串列 `.str.strip()` | `_clean_chunk_data` | 保留：落库后 `trim()` 或暂存表清洗 |
| 空搜索词行丢弃 `dropna(subset=['keyword'])` | `read_csv_chunks` | **必须保留**：补维度前 `WHERE keyword <> ''` |
| 坏行静默跳过 `on_bad_lines='skip'` | `read_csv_chunks` | **改造**：COPY 默认整批失败不跳过。改为预清洗 + **记录跳过行数到批次记录**，不再静默吞数据 |
| 文件名正则提日期 `(\d{4})[_-](\d{2})[_-](\d{2})` | `_extract_date_from_filename` | **必须保留**：V2 report_date 来源，与站点前缀解析一起做 |

**两个必须写进实现的坑：**
1. **列顺序是隐式契约**——V1/V2 都按位置映射，亚马逊一旦调整报表列序，数据会静默错位到错误字段。实现时应校验列数，并考虑用表头文字做一次 sanity check。
2. **report_date 只来自文件名**——V1 完全不用 CSV 内的日期列。V2 按日期分区，文件名日期写错会把整批写进错误分区。**必须交叉校验文件名日期与 CSV 第 21 列日期**，不一致时警告或拒绝。

## Open Questions

1. 坏行处理：预清洗到什么程度能让 COPY 不因个别坏行整批失败？跳过的行是记录计数即可，还是要落到一个错误行表供人工核对？
2. 分片粒度：大文件是"整文件 COPY 进一个暂存表"还是"分片并行 COPY"？取决于单文件 COPY 在 2C4G 上的实测耗时。
3. 原始文件本地归档的保留期与清理策略——接 OSS 之前如何避免占满磁盘。
4. 列顺序 sanity check：是否值得在导入前用第 2 行表头文字比对一次预期列名，以便亚马逊改列序时早发现而非静默错位？
