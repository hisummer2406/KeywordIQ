## Context

现状（`rewrite-import-pipeline` 交付，~80% 完成）：HTTP 上传文件 → 落应用服务器本地磁盘 →
三段式导入（`COPY` 本地 CSV → 维度 → 事实）→ 成功后 `archive_raw_file` 把本地文件 `move`
到 `IMPORT_ARCHIVE_DIR`（本地目录，注释里预留"将来换 OSS"）。

约束：应用服务器 4C8G、本地磁盘有限，周报约 1.5GB；RDS PostgreSQL 15 无法直读 OSS；导入
须有界内存、不得整文件入内存；分层约定（service 控制事务，importer/crud 不 commit）；导入
串行（advisory lock）。原始 CSV 从第一天起就应持久化——第一年热窗口内的原始报表若丢失，对
AI 训练语料是不可逆损失。

本变更把"原始文件先进 OSS"提前到入口，并让导入以 OSS 对象为源，OSS 对象即原始归档。

## Goals / Non-Goals

**Goals:**
- 前端凭临时凭证直传 OSS，GB 级字节不经应用服务器。
- 导入凭 object key 从 OSS 流式读取，喂 `COPY FROM STDIN`，内存有界。
- OSS 对象作为原始归档，取代导入后的本地 `move` 归档；批次记录可溯源到 object key。
- 日报与周报共用同一直传 + 拉取导入流程。

**Non-Goals:**
- 不改三段式导入内核（COPY→维度→事实、上一期派生、分区确保）——只换"数据从哪来"。
- 不做结构化 Parquet 归档 / MaxCompute 对接（那是路线图变更 5，另立）。
- 不实现 OSS 生命周期/冷存储分层策略（可后续单独配置 OSS 规则）。
- 不改保留清理（`add-three-month-retention` 已交付，作用于库内分区，与 OSS 原始件相互独立）。

## Decisions

**决策 1：前端直传用 STS 临时凭证 + OSS 分片上传，而非后端中转、也不首选单次签名 PUT。**
- 后端调用阿里云 STS `AssumeRole` 换取受限临时凭证（RAM 策略限定 bucket、object key 前缀、
  `PutObject`/分片相关操作、短有效期），返回给前端；前端用 OSS 前端 SDK 做分片上传。
- 备选 A：后端中转（前端传后端、后端流式转存 OSS）。否决——1.5GB 仍经应用服务器，未卸载带宽。
- 备选 B：单次签名 PUT URL。否决——1.5GB 单请求无断点续传、易失败；签名 URL 更适合小文件，
  可作为日报（~150MB）的轻量回退，但主路径按周报的 GB 量级设计，统一走 STS + 分片。
- object key 由后端按 `站点/周期/报告日期/uuid_原名` 之类规则生成并写进凭证前缀，避免前端自定义 key 越权。

**决策 2：导入从 OSS 流式读取，直接喂 `COPY FROM STDIN`，不落地临时文件。**
- `oss2` 的 `bucket.get_object(key)` 返回类文件的流对象（支持 `read(size)`），可直接作为
  `psycopg2 cursor.copy_expert('COPY ... FROM STDIN ...', stream)` 的输入——真流式、有界内存。
- 导入内核不变，仅把"打开本地文件"替换为"打开 OSS 对象流"。为兼容既有以本地路径为输入的
  `importer.import_report`，把输入抽象为"可读流"或"打开器"，本地与 OSS 两种来源都能满足。
- 备选：先 `get_object_to_file` 下载到本地临时文件再 COPY。否决——又把 GB 文件写回本地磁盘，
  与"卸载应用服务器"目标相悖；仅在需要重试/校验整文件时作为退路。

**决策 3：OSS 对象即原始归档，移除导入后的本地 `move` 归档。**
- 文件在入口即在 OSS，`archive_raw_file` 的本地 `move` 步骤删除；`IMPORT_ARCHIVE_DIR` 依赖移除
  （或降级为仅失败留存的本地退路）。批次记录持久化 object key/位置作为归档指针。
- 这直接满足"原始 CSV 从第一天存"，且比"导入成功后才归档"更早、更抗崩溃。

**决策 4：新增 `oss2` 依赖与 OSS 配置，凭证只在服务端持有。**
- `config.py` 新增 `OSS_ENDPOINT`/`OSS_BUCKET`/`OSS_REGION`/`OSS_STS_ROLE_ARN` 等；主账号
  AK/SK 或 RAM 角色凭证只在服务端，前端只拿短时 STS 凭证。同步 `.env.example`（含单位/安全默认）。

**决策 5：与 `rewrite-import-pipeline` 的排序——本变更为其后继，规格调和延后到其归档。**
- 本变更以新能力 `oss-first-ingestion` 承载（全部 ADDED），不修改仍是待同步 delta 的
  `site-scoped-import`。待 `rewrite-import-pipeline` 归档、其"导入后本地归档"要求进入主 specs 后，
  按本变更把该条要求调和为"OSS 对象即归档"（或标注被取代）。实现上，本变更直接落地 OSS 源导入，
  自然不再执行本地归档。

## Risks / Trade-offs

- [STS/RAM 配置错误导致凭证过宽或过窄] → 策略最小权限（bucket + key 前缀 + 写操作 + 短有效期）；
  key 前缀由服务端生成写入凭证，前端不可越权到其他前缀。
- [前端直传失败/中断，产生 OSS 上的孤儿对象] → 只有回传 object key 才触发导入；未被引用的对象由
  OSS 生命周期规则定期清理；导入前校验对象存在与大小。
- [从 OSS 流式 COPY 期间网络抖动中断长导入] → 导入串行 + 批次状态可重试；对象仍在 OSS，凭 key 重导；
  必要时退路为决策 2 的"先下载临时文件再 COPY"。
- [新增外部依赖 OSS 成为导入硬依赖] → 本地/测试环境需可关闭或指向兼容端点；单元测试以 fake/桩替代
  OSS 客户端，不依赖真实 OSS（遵循"单测不依赖外部真实服务"）。
- [与 rewrite-import-pipeline 并行修改同一批 upload 模块] → 明确本变更为其后继；建议其先归档再落地本变更，
  减少同文件冲突。

## Migration Plan

1. 加 `oss2` 依赖与 OSS 配置（`config.py` + `.env.example`），凭证只在服务端。
2. 新增 OSS 客户端封装：签发受限 STS 凭证、按 key 打开可流式读取的对象、校验对象存在。
3. `upload_api.py` 新增签发凭证端点；导入触发端点由「收文件」改为「收 object key」。
4. `importer`/`import_service` 把导入源抽象为可读流/打开器，接 OSS 对象流；移除本地归档步骤；
   批次记录写入 object key。
5. 前端改为直传流程（换凭证 → 分片上传 → 回传 key）。
6. 测试：单元测试用桩替代 OSS 客户端验证签发/触发/源切换逻辑；集成测试（可选，指向测试 bucket 或
   兼容端点）验证流式 COPY。
7. 回滚：保留旧的「本地文件导入」打开器作为退路，可通过配置在直传不可用时回退。

## Open Questions

- object key 命名规则与 bucket 目录约定（按 `站点/周期/日期/` 前缀？是否含 uuid 防重名覆盖）需与前端约定。
- 是否需要"上传完成校验"（大小/MD5）作为触发导入的前置，以拦截不完整对象。
- 本地/CI 环境的 OSS 替身：用 MinIO 等 S3 兼容端点，还是纯桩？影响集成测试可跑性。
- 与 `rewrite-import-pipeline` 的落地顺序：先归档它再实施本变更，还是并行（需解决同文件冲突）。
