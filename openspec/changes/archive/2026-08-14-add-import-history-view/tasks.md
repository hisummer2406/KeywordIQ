## 1. 后端：crud 层与既有查询归位

- [x] 1.1 新建 `app/table/upload/import_batch_crud.py`，实现 `list_batches`（站点/周期/状态
      三项可选筛选 + 分页 + `created_at DESC`，返回 `(rows, total)`）、`get_batch(batch_id)`、
      `recent_batches(period, limit)`。纯查询组装，不 commit。
- [x] 1.2 把 `upload_api.py` 的 `/processing-status` 内联 `select` 改为调用
      `import_batch_crud.recent_batches`，响应形状与字段保持完全不变。
- [x] 1.3 单测 `test/test_import_history_unit.py`：断言 `list_batches` 组装出的 SQL 含
      预期的 WHERE/ORDER BY/LIMIT/OFFSET（编译 statement 断言，不连库）。

## 2. 后端：service 层与准入判定

- [x] 2.1 新建 `app/table/upload/import_history_service.py`，实现 `query_batches`：
      站点码经 `normalize_site_code`（非法即抛 `BadRequestError`，不回退默认）、周期与状态
      按 `VALID_PERIOD_TYPES` / `VALID_STATUSES` 校验、`per_page` 超 100 抛
      `BadRequestError`（不截断）。
- [x] 2.2 在 `upload_api.py` 提取 `/oss/import` 中「前缀归属 + `stat_object` 存在与大小」
      两道校验为共用函数，`/oss/import` 改为调用它，行为不变。
- [x] 2.3 在 `import_history_service.py` 实现 `retry_batch`：按设计决策 4 的顺序做准入判定
      （不存在→404 / 非 FAILED→409 / `object_key` 空→422 / 前缀不符→400 / 对象不存在或
      超限→4xx），通过后新建批次记录并交后台 `_run_oss_import_bg`。错误消息不回显 object key。
- [x] 2.4 单测补充到 `test/test_import_history_unit.py`：逐条覆盖 2.1 与 2.3 的判定分支
      （service 层直测，OSS 调用以假对象替身）。

## 3. 后端：端点与契约

- [x] 3.1 新增 `GET /api/upload/batches`，挂 `require_permission(UPLOAD_IMPORT)`，
      经 `protable()` 返回 `{data,total,success}`。
- [x] 3.2 新增 `POST /api/upload/batches/{id}/retry`，挂 `require_permission(UPLOAD_IMPORT)`，
      返回新建批次 id 与归属三元组。
- [x] 3.3 新建 `test/test_import_history_api.py`（TestClient + 依赖覆盖，不连库）：
      两个端点无 token 必拒 401；列表响应形状是 `{data,total,success}`；`per_page=500` 返 400；
      非 FAILED 重导返 409；空 `object_key` 返 422；不存在的 id 返 404。
- [x] 3.4 集成测试追加到 `test/test_import_service_integration.py`：重导新建记录、原 FAILED
      记录的状态与错误信息保持不变（计数带 `WHERE site=... AND report_date=...` 作用域）。

## 4. 前端：共享状态组件与接口

- [x] 4.1 新建 `frontend/src/components/ImportStatus/`，把 `pages/Upload/index.tsx` 的
      `STATUS_META` 与 `StatusCell` 搬过去；上传屏改为引用，渲染结果不变
      （`pages/Upload/index.test.tsx` 现有断言应仍然通过）。
- [x] 4.2 `frontend/src/services/upload.ts` 新增 `fetchImportBatches`（ProTable request 形状，
      `current`/`pageSize` 映射为 `page`/`per_page`）与 `retryImportBatch(id)`，
      补 `ImportBatchRow` 类型。

## 5. 前端：导入历史屏

- [x] 5.1 新建 `frontend/src/pages/ImportHistory/index.tsx`：ProTable + 站点/周期/状态三个
      `valueEnum` 筛选列，列含批次、站点、周期、报告日期、状态（Tag + 失败原因 ellipsis+tooltip）、
      写入行数/源文件行数、耗时、导入时间、操作。不轮询。
- [x] 5.2 重导按钮：仅 `FAILED` 行显示；`FAILED` 且 `object_key` 为空时置灰并 tooltip 说明
      「原始文件未留存，请重新上传」；点击后调接口并 `actionRef.reload()`。
- [x] 5.3 `frontend/.umirc.ts` 把 `/upload` 改成带子路由的父级（`/upload/file` 上传、
      `/upload/history` 导入历史，`/upload` 重定向到前者），`src/app.tsx` 改
      `layout: 'mix'` + `splitMenus: true`，让一级留顶栏、二级出左侧。
- [x] 5.4 新建 `frontend/src/pages/ImportHistory/index.test.tsx`：三种行的重导入口态
      （可点 / 置灰 / 不显示）、重导后触发 reload、筛选条件正确映射为请求参数。

## 6. 收尾验证

- [x] 6.1 `uv run pytest test/ -q` 全绿；本地测试库可用时确认集成测试未被 skip。
- [x] 6.2 `cd frontend && pnpm lint && pnpm tsc && pnpm test` 全绿。
- [x] 6.3 更新 `CLAUDE.md`：在导入一节补一句导入历史屏与重导的准入规则（只允许 FAILED、
      重导新建记录）。
