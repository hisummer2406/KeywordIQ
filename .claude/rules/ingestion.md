---
paths:
  - "app/table/upload/**/*.py"
  - "app/jobs/**/*.py"
  - "scripts/backfill*.py"
  - "scripts/check_oss.py"
  - "test/test*import*.py"
  - "test/test*oss*.py"
  - "frontend/src/pages/Upload/**/*"
---

# 导入实现约束

- 改前同时读 analysis 规则和 [导入规格导航](../../openspec/guides/ingestion.md)。
- 凭证交出前先落 UPLOADING，落库失败不能签发；STS 前缀受限，报告期次取文件名，不取上传时间。
- GB 文件直传、流式 COPY；不恢复 V1 的全量内存处理或并行分片。
- 导入共用咨询锁；QUEUED 必须由独立轮询接手，不靠前次结束链式接力；源不可重建就失败，不永久排队。
- UPLOADING 回收时限由 STS 有效期推导；对象存在转排队前重验大小，不能绕过正常入口校验。
- 重导复用 verify_source_object 和 run_import，先做存在/状态/前缀/对象/大小校验，再交后台任务。
- 失败重导新建批次保留旧记录；不要擅自开放成功批次重导或用原记录抹掉失败历史。
- 上传看板与分页历史共用 CRUD；历史不轮询，不将超限请求静默截断。
- STS 越权、OSS CORS/ETag 需要 check_oss 实测；单元绿不能证明外部配置已就绪。
- 改路径后运行 verify-import；改首次证据写入须验证乱序与重复导入结果一致。
