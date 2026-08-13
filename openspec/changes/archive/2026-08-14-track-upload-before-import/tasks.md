# 任务

## 1. 状态与配置

- [x] 1.1 `import_batch_model.py` 新增 `STATUS_UPLOADING` 并加入 `VALID_STATUSES`；
      `status` 列注释补上新状态
- [x] 1.2 迁移：`COMMENT ON COLUMN import_batch.status`（纯注释，不改结构）。
      改模型注释必须配迁移，否则下一次 autogenerate 会替你生成
- [x] 1.3 `config.py` 加派生属性 `upload_stale_seconds = OSS_STS_DURATION_SECONDS + 300`
      （**不新增配置项**，理由见 design 决策 2）

## 2. 签发凭证时落记录

- [x] 2.1 `import_service.create_upload_batch(...)`：落一条 `UPLOADING` 批次，返回记录
- [x] 2.2 `/oss/upload-credentials` 先落库、后签发；落库失败直接抛错不签发凭证
      （design 决策 6）。响应体加 `batch_id`
- [x] 2.3 `import_service.claim_upload_batch(object_key)`：按 object key 认领 `UPLOADING`
      批次；认领不到返回 None
- [x] 2.4 `run_import` 接受可选的既有批次（`batch_id`），有则推进它、无则新建
- [x] 2.5 `/oss/import` 走认领路径

## 3. 收尾（回收）

- [x] 3.1 `import_service.reap_stale_uploads(...)`：超时的 `UPLOADING` 批次逐条按
      「对象在不在」分支——在则称量大小、置 `QUEUED`（超限则 `FAILED`）；不在则 `FAILED`
- [x] 3.2 `import_queue` 的轮询每轮调一次回收，异常不得让轮询任务退出

## 4. 测试

- [x] 4.1 单元：签发落库失败 → 不签发凭证、不返回凭证
- [x] 4.2 单元：`/oss/import` 认领已有 `UPLOADING` 批次，不新增第二条；认领不到时新建
- [x] 4.3 单元：回收的三条分支（未超时不动 / 对象在则转 `QUEUED` / 对象不在则 `FAILED`）
- [x] 4.4 单元：回收路径对超限对象判 `FAILED`，不放它进导入
- [x] 4.5 集成：签发 → 不触发导入 → 回收接手 → 数据真的落库（跑真库）

## 5. 收尾验证

- [x] 5.1 `uv run pytest test/ -q` 全绿，确认集成测试未被整片 skip
- [x] 5.2 CLAUDE.md 导入一节补上上传阶段的状态与回收规则

---

**收尾记录**（2026-08-12）：全部 15 项完成。验证：`ruff check .` 通过；
`TEST_DATABASE_URL=... uv run pytest test/ -q` → **413 passed, 1 skipped**（集成测试真跑，
唯一 skip 是需要真实 OSS 的那个；不是 45 个整片 skip 的假绿）。迁移在 `keywordiq_test` 上
`upgrade head` 通过，`test_models_match_migrated_schema` 仍为空差异。

**范围外但被这次改动碰到的一处**：`test_conventions.py` 的 `settings_fields` 会把
`ClassVar` 也当成配置项，要求它出现在 `.env.example`——而 pydantic 不把 ClassVar 当字段，
写进模板就是一行「配了也不生效」的假配置。已修（附正反 fixture）。
