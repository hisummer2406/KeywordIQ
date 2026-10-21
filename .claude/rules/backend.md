---
paths:
  - "app/**/*.py"
  - "alembic/**/*.py"
  - "config.py"
  - "database.py"
  - "migration_scope.py"
  - "main.py"
  - "scripts/**/*.py"
  - "test/**/*.py"
---

# 后端实现约束

- 保持 API → service → CRUD 分层；不要在新 CRUD 中独立 commit 或在 API 盖掉业务错误码。
- 失败抛 `APIError` 子类，响应形状以 [错误契约](../../openspec/specs/api-error-contract/spec.md) 为准；不要返回失败的 200 列表。
- 内部 SQL、对象 key、路径和栈只写 log_detail；Pydantic 错误白名单重建 field/msg，不透传 input。
- 不启用 FastAPI debug traceback；request_id 沿请求、日志与响应传递。
- 流式错误在开流前判定；生成器自建会话，避免消费已清理的请求 Session。
- BackgroundTasks 自捕获并 logger.exception 留栈；响应处理器无法补救已交出的后台失败。
- 模型使用配置 schema；业务新表登记 V2_TABLES，ops 表登记 OPS_TABLES，否则 autogenerate 看不见。
- 分区表/物化视图手写迁移，离线核对分区 DDL；模型注释与迁移一致，共用注释只维护一份。
- 真实数据库迁移到 head 后检查模型差异；单元 mock 不能代替分区、锁或迁移回归。
- 手写约定检查先看 ruff 是否能表达，必须配正反 fixture；基线新增违规失败，修好后删除基线项。
- 不调大连接池或增加并行导入来掩盖慢查询；先核预算/执行计划，容量与发布操作另读 operations 规则。
