## Why

导入批次目前只有一个出口：上传屏的「最近导入状态」看板，写死 `limit(5)`、无分页、无筛选、
每 5 秒整表轮询覆盖。它服务的是「我刚传的文件跑到哪了」，而不是「上周 DE 的周报到底导没导」。
第 6 条以后的批次在界面上根本够不着，用户看不到时也无从判断是「没传过」还是「被截断了」。

同时，失败批次的原始文件已经持久留在 OSS（`import_batch.object_key`），但界面上没有任何
途径用它——修复一次失败导入只能重新上传一遍 GB 级文件。

本变更影响**日报与周报两条路径**（两者共用同一份 `import_batch` 记录与同一套导入内核）。

## What Changes

- 新增「导入历史」屏，挂在**「数据上传」一级菜单下的左侧二级菜单**里（`/upload/history`，
  与之并列的是「上传文件」`/upload/file`；旧地址 `/upload` 重定向到后者）。提供
  **站点 / 周期 / 状态**三项筛选与分页，按导入时间倒序。权限沿用 `upload:import`，不新增权限码。
- **布局从 `top` 改为 `mix` + `splitMenus`**：一级菜单留在顶栏，二级下沉到左侧。只有带子菜单
  的一级项才展开侧栏，分析查询没有子项，其宽表不因此损失横向空间。
- 新增 `GET /api/upload/batches`：ProTable 契约 `{data,total,success}` 的分页批次列表。
- 新增 `POST /api/upload/batches/{id}/retry`：**仅 `FAILED` 批次**可凭已留存的 object key 重导。
  重导**新建一条批次记录**，不改写原记录——原失败记录及其错误信息是排查依据，不能被抹掉。
- 补齐分层：新增 `import_batch_crud.py` 与 `import_history_service.py`。现有 `/processing-status`
  在 API 层内联 `select` 的查询一并挪进 crud，两个入口共用一份查询代码。
- 前端把上传屏内的状态文案（`STATUS_META`）抽成共享组件，两屏引用同一份——否则同一个状态
  会在两个屏上叫不同名字。
- 上传屏的「最近导入状态」看板**保持原样**（仍是 5 秒轮询的最近 5 条）。它与历史屏职责不同：
  一个盯进度，一个查历史。历史屏因此不轮询。

## Capabilities

### New Capabilities

- `import-history-view`: 导入批次的历史查询与失败重导——分页与筛选的行为边界、重导的准入
  条件（状态、原始文件留存、对象仍在）、重导的记录方式，以及重导与既有串行锁/排队机制的关系。

### Modified Capabilities

（无。`oss-first-ingestion` 已确立「批次记录携带 object key 以供溯源与重导」，本变更是对该
能力的消费，不改变其要求。）

## Impact

**后端**

- 新增 `app/table/upload/import_batch_crud.py`、`app/table/upload/import_history_service.py`
- 修改 `app/table/upload/upload_api.py`：新增两个端点；`/processing-status` 改为调用 crud
- 复用 `app/table/upload/oss_client.py` 的 `is_key_within_allowed_prefix` / `stat_object`
  与 `import_service.run_import`（重导因此同样受 `pg_try_advisory_lock` 串行化，
  拿不到锁进 `QUEUED` 由 `import_queue` 接手）

**前端**

- 新增 `frontend/src/pages/ImportHistory/`、`frontend/src/components/ImportStatus/`
- 修改 `frontend/.umirc.ts`（把 `/upload` 改成带子路由的父级）、
  `frontend/src/app.tsx`（`layout: 'mix'` + `splitMenus`）、
  `frontend/src/services/upload.ts`（两个接口）、
  `frontend/src/pages/Upload/index.tsx`（改引用共享状态组件）

**不涉及**：数据库迁移（`import_batch` 表结构不变，现有索引 `idx_import_batch_created` /
`idx_import_batch_site_period` / `idx_import_batch_status` 已够用）、新增配置项、导入内核逻辑。

**已知范围外**：`import_batch` 表没有清理机制，事实表的三个月保留窗口不覆盖它。行数很小
（每天个位数），本次不处理。
