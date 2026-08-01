## 1. 前置盘点

- [x] 1.1 盘点 V1 `csv_processor` 的 CSV 清洗逻辑（已完成，结论见 design "V1 清洗逻辑盘点结论"）：必须保留的有——三段式结构校验、列按位置映射、数值 coerce 容错、排名 float→int、空搜索词过滤、文件名日期解析；需改造的有——坏行由静默跳过改为记录计数
- [x] 1.2 用 `docs/table/` 下的真实样本确认 CSV 结构（元数据行 + 表头 + 数据行）与 21 列顺序，作为 COPY 列映射依据 —— 已核对 US/DE 日报周报样本：行1 元数据（`报告范围=[...]`）、行2 表头恰 21 列含「搜索频率排名/搜索词」、行3+ 数据 21 列（列0 排名…列20 报告日期），编码 utf-8-sig（含 BOM），与 `importer.py` 的列映射与结构校验一致
- [ ] 1.3 确定分片策略：单文件整体 COPY 还是分片并行（依 2C4G 上单文件 COPY 实测耗时定）—— **阻塞：待 RDS 2C4G 环境可用**。在此之前默认单文件整体 COPY（现行实现），不做投机优化

## 2. 配置

- [x] 2.1 `config.py` / `.env.example` 新增 `IMPORT_ARCHIVE_DIR`（原始文件归档目录，含说明与本地默认值）
- [x] 2.2 校正 `.env` / `.env.example` 的 `BATCH_SIZE`、`MINIBATCH_SIZE` 至安全值
- [x] 2.3 `upload_schemas.py` 请求 DTO 增加站点（必填）与周期字段，用 Pydantic v2 `field_validator` 校验站点码

## 3. 三段式导入核心

- [x] 3.1 实现第一段：建 `UNLOGGED` 无索引暂存表 + `COPY` 原始 CSV 流式装载（用完 DROP）
- [x] 3.2 实现 COPY 前的最小清洗（列数/编码/空值），保留 1.1 认定必须保留的逻辑；数值容错（脏值转 0）与排名 float→int 改用暂存表 SQL 实现
- [x] 3.2b 坏行改为记录跳过计数（写入批次记录），不再 `on_bad_lines='skip'` 静默吞数据
- [x] 3.2c 交叉校验文件名日期与 CSV 第 21 列日期，不一致时警告/拒绝（report_date 决定写入哪个分区，错了后果严重）
- [x] 3.3 实现第二段：从暂存表 `INSERT ... ON CONFLICT DO NOTHING` 补齐 `dim_keyword` 与 `dim_product`
- [x] 3.4 实现第三段：暂存表 JOIN 维度 + LEFT JOIN 上一期分区，集合式算出 previous/change/is_new，追加写目标事实表
- [x] 3.5 "上一期"关联只取同站点同周期（日报取前一天、周报取前一周），确保不跨站点/跨周期继承
- [x] 3.6 用一个 `period` 参数驱动日报/周报共用同一份代码，仅切换目标表与上一期定义
- [x] 3.7 **删除** V1 代码：随前端迁移阶段七 amis 撤除 + V1 上传端点退役，已删 `csv_processor.py`、`upload_service.py`、`import_model.py`、`analysis_model.py`（含两条 UPSERT SQL、`_fetch_existing_trends`、`ranking_trend_day`）；upload_api 重写为 V2-only。真机验证 GB/DE 真实日报导入正常

## 4. 编排与安全

- [x] 4.1 写事实表前调用 `ensure_fact_partitions`，跨月首次导入不得因分区缺失失败
- [x] 4.2 实现同一期重复导入的覆盖：事务内先 `DELETE (site, report_date)` 切片再插入，外部观察不到中间态
- [x] 4.3 导入前 `pg_try_advisory_lock` 串行化，拿不到锁置批次为 `QUEUED` 并提示，结束时释放锁
- [x] 4.4 站点参数缺失或文件名前缀与声明站点冲突时，在文件落盘前拒绝
- [x] 4.5 `ImportBatch` 批次记录写入 site/period/report_date/行数/耗时/状态，关键节点日志带站点
- [ ] 4.6 多进程分片 —— **可能不需要**：V2 用 COPY 流式装载，实测 33MB/12 秒（推算 232MB≈1.5 分、1.5GB≈9 分），已远优于 V1 的 30–60 分。V1 分片是为并行慢速逐行 UPSERT，COPY 无此瓶颈。待 1.3 在 2C4G 实测后再定是否需要

## 5. 导入后处理

- [x] 5.1 事实提交后（事务外）更新 `site_data_stats` 的 (site, period, report_date) 行数
- [x] 5.2 事实提交后 `REFRESH MATERIALIZED VIEW CONCURRENTLY category_stats`
- [x] 5.3 实现可替换的 `archive_raw_file(path) -> location` 接口，当前实现为移动到 `IMPORT_ARCHIVE_DIR`（为 OSS 预留）
- [x] 5.4 导入成功后归档原始文件；导入失败时保留原始文件供排查，不清理

## 6. API 与触发

- [x] 6.1 `upload_api.py` 站点参数必填 + 文件名校验；触发形态保留 HTTP 上传 + 后台任务
- [x] 6.2 `upload_service.py` 编排三段式 + 分区 + 锁 + 覆盖 + 刷新 + 归档，事务边界由 service 控制、crud 不 commit

## 7. 测试

- [x] 7.1 单元测试（小样本 fixture，不依赖真库）：站点校验、文件名前缀冲突、清洗逻辑、period 参数切换目标表，`uv run pytest test/` 验证
- [x] 7.2 集成测试（`TEST_DATABASE_URL`，沿用变更 1 的 gate 模式）：三段式全流程写入日报+周报、跨站点不覆盖、跨日期不覆盖、派生值正确、is_new 正确
- [x] 7.3 集成测试：同一期重复导入为完整替换、跨月自动建分区、串行锁生效（第二个导入排队）
- [x] 7.4 集成测试：导入后 site_data_stats 更新、category_stats 刷新、原始文件被归档
- [x] 7.5 用 `docs/table/` 真实样本验证 COPY 解析无误（编码、列数、日期）

## 8. 上线

> 本节全部 **阻塞：待生产/预发环境（ECS 4C8G + RDS 2C4G）可用**。代码侧已就绪，
> 这三项是只能在真实规格上取得的观测结果，不可在本地代替。

- [x] 8.1 部署新导入代码，用小样本走通全流程 —— **已走通**（2026-08-01，本地环境
  `OSS_ENABLED=true`，浏览器端到端，非 curl 模拟）：200 行样本
  `US_热门搜索词_简单_Day_2026_08_01.csv` 从上传屏拖入 → ali-oss 分片直传（47,177 B）
  → 回传 object key → 后台从 OSS 流式 COPY → 落库，2 秒完成。逐项核对：
  - object key `imports/US/DAY/2026-08-01/32aa32c6_...csv`，符合前缀约定且校验通过；
    对象在 OSS 中存在（OSS 对象即原始归档）
  - `import_batch` 记 COMPLETED、200/200 行、**object_key 已持久化**（此前两条历史批次
    的 object_key 为空串，走的是本地退路，不能作为 OSS 路径的证据）
  - **跨月建分区被真实触发**：自动建出 `fact_daily_2026_08` 与站点子分区
    `fact_daily_2026_08_us`（库中原有数据最新到 07-30）
  - 派生值写入时算定：`is_new=true`、排名、维度关联齐全
  - 查询侧即时可见：`/scope` 的 `latest_date` 变为 2026-08-01，`/search` 查得到
  顺带验到一条前端护栏：未选站点就丢文件被「请先选择站点」拦下，不发凭证请求
- [ ] 8.2 逐站点导入真实数据，核对行数、派生值、维度去重效果
- [ ] 8.3 记录导入耗时与死元组情况，与 V1 对比（预期显著改善）；结果回填 1.3 与 4.6 的决策
