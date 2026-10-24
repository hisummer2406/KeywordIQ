## Why

Refs #148。用户反馈报表导入成功后，市场分析长时间加载不出对应期次，且会拖慢其他页面。

2026-10-22 在本地 PG15 一次性库用真实 US 日报（43–47 万行/期）复现，对线上代码生成的 SQL 做 `EXPLAIN ANALYZE`：

- 新期次不在叶子分区的统计信息（MCV）里，规划器把当期估成 1 行（实际 37.7 万），选择逐行嵌套循环。默认首页 18 s、排除品牌词 31 s、首次入榜筛选 >120 s。
- 只对写入的叶子分区 `ANALYZE`（0.65 s）后全部回到 1.3 s 以内。
- 自动分析阈值是分区行数的 10%，月中单日导入达不到；而导入在写 `site_data_stats` 后新期次即可选，此前从不 `ANALYZE`。
- 交互查询没有执行超时，一条坏计划会一直占用异步池连接，池耗尽后其他页面一起卡住。

## What Changes

- 导入在新期次对外可见之前，对本次写入的事实叶子分区执行 `ANALYZE`；日报、周报、新期次与同一期重导同路径。
- 批次新增查询统计状态（待更新/就绪/失败）。失败不回滚、不隐藏已提交数据；导入轮询自动重试，用尽后标记失败，并在上传看板与导入历史可见。
- 异步引擎（交互读路径）设置数据库执行超时，默认 30 s，可配；导入与导出走同步引擎，不受影响。超时返回 503 和专用错误码。
- 分析查询每个 worker 限制并发数（默认 4），排队等待有上限（默认 5 s），超时返回 503 繁忙；保证轻量页面始终拿得到连接。
- 不在本变更内：重复查询合并、取消过期请求、品牌词排除与计数优化、结果缓存、分阶段耗时预算，属于 #148 的「后续」部分，另开 Issue，待本变更上线后有测量数据再做。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `site-scoped-import`：新增「新期次可见前刷新查询统计」「查询统计刷新失败可见并自动恢复」。
- `import-history-view`：新增「批次展示查询统计状态」。
- `async-request-execution`：新增「交互查询有执行时间预算」「分析查询并发有上限且排队有界」。
- `api-error-contract`：修改「失败响应遵循单一契约」，状态码映射补上 503。

## Impact

- 代码：`app/table/upload/import_service.py`、`import_queue.py`、`import_batch_model.py`、`upload_schemas.py`；`database.py`；`app/table/analysis/analysis_api.py` 或 `analysis_service.py`；`app/common/error_handlers.py`、`errors.py`；`config.py`、`.env.example`；前端 `ImportStatusCell`。
- 迁移：`import_batch` 新增两列，可空/有默认值，旧批次为 NULL，表示不适用。
- 运维：已在线、统计过期的期次不在本变更内处理。是否对生产补一次 `ANALYZE` 需单独授权。
