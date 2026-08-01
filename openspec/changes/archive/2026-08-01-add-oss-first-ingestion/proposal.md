## Why

表格文件（尤其周报约 1.5GB）当前先落到应用服务器本地磁盘再导入，占用 4C8G 服务器有限的
磁盘与内存；且原始文件要到导入成功后才归档——上传到导入之间若进程崩溃，原始报表可能永久
丢失（对 AI 训练语料是不可逆损失）。改为「先直传 OSS，再从 OSS 做导入」：原始文件在入口即
持久化，GB 级字节流从应用服务器卸载。**本变更同时影响日报路径与周报路径**。

## What Changes

- **上传改为前端直传 OSS**：后端签发临时凭证（STS / 签名 URL），前端把 CSV 直接 PUT 到 OSS，
  字节不经应用服务器；后端只拿到 object key。
- **导入改为以 OSS object 为源**：导入任务凭 object key 从 OSS 流式读取，喂给 `COPY FROM STDIN`
  （RDS 不能直读 OSS，由后端中转流，保持有界内存）。
- **OSS object 即原始归档，取代导入后的本地归档**：移除 `rewrite-import-pipeline` 的
  `archive_raw_file` 本地 `move` 步骤与对 `IMPORT_ARCHIVE_DIR` 的依赖——文件在入口即在 OSS，
  OSS object 本身就是原始留档。
- **导入批次记录携带 OSS object key/位置**，支持溯源与重导。
- **新增 OSS 配置**（endpoint / bucket / region / STS 角色等）并同步 `.env.example`；新增 `oss2` 依赖。
- **BREAKING（内部契约）**：前端上传契约改变——由「上传文件到后端」变为「向后端换取临时凭证 →
  直传 OSS → 回传 object key 触发导入」；原有分块直传后端的接口被取代。

## Capabilities

### New Capabilities
- `oss-first-ingestion`: 表格文件先经临时凭证直传 OSS，再由后端凭 object key 从 OSS 流式拉取
  做导入；OSS object 即原始归档，导入以之为唯一数据源。

### Modified Capabilities
<!-- site-scoped-import 的「导入后原始文件本地归档」要求被本变更取代，但该能力目前仍是
     rewrite-import-pipeline 的待同步 delta，尚未进入主 specs。为避免修改未同步的规格，
     此处不放 delta；待 rewrite-import-pipeline 归档后，按本变更调和该条要求（见 design 排序说明）。 -->

## Impact

- **影响路径**：日报路径与周报路径两者（共用同一套直传 + 拉取导入流程）。
- **代码**：
  - `app/table/upload/upload_api.py`：新增签发临时凭证的端点；导入触发由「收文件」改为「收 object key」；移除/取代分块直传后端接口。
  - 新增 OSS 客户端封装（签发凭证、按 key 打开可流式读取的对象）。
  - `app/table/upload/importer.py` / `import_service.py`：导入源从本地路径改为 OSS 对象流；移除 `archive_raw_file` 本地归档步骤。
  - `app/table/upload/import_batch_model.py` / `upload_schemas.py`：批次记录与 DTO 增加 object key/位置。
  - `config.py` / `.env.example`：新增 OSS 相关配置。
- **依赖**：新增 `oss2`（阿里云 OSS Python SDK）。
- **前端**：上传交互改为直传 OSS（换取凭证 → PUT → 回传 key）。
- **运行时**：应用服务器不再承载 GB 级文件落盘；导入内存占用由流式 COPY 保证有界。
- **排序依赖**：本变更取代的「本地归档」要求属于仍在进行的 `rewrite-import-pipeline`；两者的规格调和在该变更归档后进行。
