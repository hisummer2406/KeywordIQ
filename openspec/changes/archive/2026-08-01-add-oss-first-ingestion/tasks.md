## 1. 依赖与配置

- [x] 1.1 `uv add oss2` 加入阿里云 OSS SDK 依赖（并加 `aliyun-python-sdk-sts` 供 STS AssumeRole）
- [x] 1.2 `config.py` 新增 OSS 配置（`OSS_ENABLED`/`OSS_ENDPOINT`/`OSS_REGION`/`OSS_BUCKET`/`OSS_KEY_PREFIX`/`OSS_ACCESS_KEY_*`/`OSS_STS_*`），凭证只在服务端
- [x] 1.3 `.env.example` 同步新增 OSS 配置项（含说明与安全默认；`OSS_ENABLED=false` 走本地退路）

## 2. OSS 客户端封装

- [x] 2.1 `oss_client.issue_upload_credentials`：STS `AssumeRole` 签发受限临时凭证（RAM 策略限定 bucket、key 前缀、仅写操作、短有效期）
- [x] 2.2 `build_object_key` / `key_prefix_for`：`前缀/站点/周期/日期/uuid_原名`，前缀写入凭证策略，`basename` 防路径穿越
- [x] 2.3 `object_stream`（返回支持 `read(size)` 的流）/ `object_exists`；对象缺失抛 `OSSObjectNotFound`
- [x] 2.4 STS/OSS 客户端惰性 import 且可注入（`assume_role`/`bucket` 参数），单测用桩替代，不依赖真实 OSS

## 3. 上传 API 改造

- [x] 3.1 `POST /oss/upload-credentials`：入参站点/文件名/周期，返回临时凭证 + object key（`_resolve_import_meta` 复用站点/日期/周期解析）
- [x] 3.2 `POST /oss/import`：凭 object key 触发；校验前缀合法（`is_key_within_allowed_prefix`）、对象存在后交后台
- [x] 3.3 前端不再把字节传后端（新流程走直传；旧 `/import-csv` 保留为本地退路，非直传后端）
- [x] 3.4 `upload_schemas.py` 增 `UploadCredentialRequest` / `OSSImportRequest`（含站点校验）

## 4. 导入源切换为 OSS

- [x] 4.1 新增 `import_source.py`：`ImportSource` 抽象 + `LocalFileSource` / `OSSObjectSource`；`importer` 的 `validate_structure`/`_stage_csv`/`import_report` 改吃 `source`
- [x] 4.2 `OSSObjectSource` 从 OSS 流式读（`_RawReadable`+`BufferedReader`+`TextIOWrapper`，utf-8-sig），喂 `COPY FROM STDIN`，不落地临时下载
- [x] 4.3 `import_service.run_import` 改吃 `source` + `object_key`；移除 `archive_raw_file` 主流程调用；批次记录写 object key
- [x] 4.4 `import_batch_model` 增 `object_key` 列 + Alembic 迁移 `b1a2c3d4e5f6`（离线 `--sql` 已核对 `ADD COLUMN`）
- [x] 4.5 保留 `LocalFileSource` + `/import-csv` 作为本地退路（`OSS_ENABLED=false`）

## 5. 测试

- [x] 5.1 `test_oss_ingestion_unit.py`：凭证 RAM 策略最小权限（bucket/前缀/仅写/时长）与 object key 生成，用桩替代 OSS
- [x] 5.2 同上：object key 前缀越权校验（拒他站/他期）、对象缺失抛 `OSSObjectNotFound`
- [x] 5.3 同上：`ImportSource` 抽象——本地流与 OSS 流统一驱动 `validate_structure`，utf-8-sig 解码、底层流关闭
- [x] 5.4 `test_oss_ingestion_integration.py`：层一（`TEST_DATABASE_URL` 即运行）以 OSS 形态流式源 → 真实 Postgres 的 COPY → 事实落库 + object_key 持久化 + 同期覆盖，已对真库跑通（全量 136 passed）；层二（另需 `TEST_OSS_*`）覆盖真实 `put_object`→`object_stream`→落库回路，无云凭证时跳过。测试引擎用 `NullPool` 规避测试间连接池滞留与咨询锁抢锁的时序问题

## 6. 前端与文档

- [x] 6.1 前端直传流程（换凭证 → OSS 分片上传 → 回传 key）—— 已随前端迁移阶段五落地：`frontend/src/services/ossUpload.ts` + `ali-oss@6.22`，上传屏 `frontend/src/pages/Upload/index.tsx` 走 `customRequest`（原备注「仓库内无前端构建」写于 AntD Pro 迁移之前，已失效）
- [x] 6.2 更新 `CLAUDE.md` 导入链路：直传 OSS、以 OSS 对象为源、OSS 对象即原始归档、本地退路
- [x] 6.3 记录 object key 命名规则与 bucket 目录约定（写入 `CLAUDE.md` 导入链路段）

## 7. 与 rewrite-import-pipeline 的调和

- [x] 7.1 把 `site-scoped-import` 的「导入后原始文件本地归档」要求调和为「OSS 对象即归档」 —— 已直接改在 `rewrite-import-pipeline/specs/site-scoped-import/spec.md` 的「原始文件在导入后被保留」要求上（无需等归档，归档时随该 change 一并进主 specs）。配套清理：删除死代码 `_legacy_archive_raw_file` 与 `IMPORT_ARCHIVE_DIR` 配置（本地退路按设计不归档，该函数无任何调用者）
