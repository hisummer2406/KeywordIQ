# CSV 与 OSS 导入

本指南是根目录 [CLAUDE.md](../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。
文中代码与命令路径均相对仓库根目录；主文件规定跨域红线，本指南保留该域的细则、理由和历史证据。
历史测量、上线状态与人工验证记录只证明记录时状态，不替代本次验证。

### Data Import Flow

**V2 OSS-first 导入（现行，日报周报同一套流程）**：表格文件**先直传 OSS，再从 OSS 导入**。

1. **签发直传凭证** `POST /oss/upload-credentials`（`upload_api.py` → `oss_client.issue_upload_credentials`）：
   后端调 STS `AssumeRole` 换取受限临时凭证（RAM 策略限死到指定 bucket + object key 前缀 + 仅写
   操作 + 短有效期），前端凭此**直传 OSS**，GB 级字节不经应用服务器。服务端凭证只在后端持有。
   **同时落一条 `UPLOADING` 批次记录，且落库先于交出凭证**——直传那一段服务端全程不知情，
   没有这条记录，用户关掉标签页就等于什么都没发生过（他以为传上去了，历史屏查不到）。
   顺序反过来则是「能用的凭证 + 不存在的记录」，正是要消灭的那个状态；落库失败就不签发。
2. **触发导入** `POST /oss/import`：前端回传 object key；后端校验 key 落在授权前缀内、对象存在，
   **按 object key 认领第 1 步那条 `UPLOADING` 批次**（认领不到就新建——回收可能刚把它判失败，
   或调用方跳过签发直接触发），再交后台任务 `run_import`。
3. **三段式导入**（`importer.py`，源经 `ImportSource` 抽象）：从 OSS 对象**流式** `COPY` → UNLOGGED
   暂存表 → 补齐维度 → 关联上一期算派生值、**追加**写事实分区（写前必调 `ensure_fact_partitions`）。
   内存有界，不整文件落地。

   **维度补齐只在属性真变时才写。** `dim_product` 的 UPSERT 带
   `WHERE dim_product.title IS DISTINCT FROM EXCLUDED.title`，这条 WHERE **不是冗余**：
   PostgreSQL 的 UPDATE 无论新旧值是否相同都写新元组、标死旧元组、并在该行涉及的每个索引
   上插新条目——而 title 上挂着这张表最大的索引（GIN trgm，1.3 GB）。无条件更新意味着每次
   导入重写**所有已存在的商品行**（一份日报约 40 万行 × 3 个槽位 ≈ 120 万个 ASIN 提及，
   绝大多数早已在库里），而且会自我加速恶化：维度表越大，"已存在"的比例越高，白做的更新
   越多，承受它们的索引也越大。实测同一份 10 万行日报因此从 54 秒退化到 416 秒（issue #33）。
   用 `IS DISTINCT FROM` 而非 `<>` 是因为后者遇 NULL 结果为 NULL，将来放宽可空性会静默
   退化成"NULL 的行永远不更新"。`dim_keyword` / `dim_brand` / `dim_category` 走
   `DO NOTHING` + `NOT EXISTS` 预筛，本来就不重写既有行。
4. **OSS 对象即原始归档**：文件入口即持久化于 OSS，不再做导入后本地归档；批次记录（`import_batch.object_key`）
   持久化 object key 供溯源与重导。失败也无碍——OSS 对象仍在，凭 key 重导。

**object key 约定**：`<OSS_KEY_PREFIX>/<SITE>/<PERIOD>/<report_date>/<uuid>_<原文件名>`，
如 `imports/US/DAY/2026-07-18/ab12cd34_US_..._Day_2026_07_18.csv`。前缀由服务端生成并写进凭证策略，
前端无法越权到其他站点/期次；`report_date` 只来自文件名（分区归属的权威来源）。

**配置**：`OSS_*`（见 `.env.example`）；`OSS_ENABLED=false` 时走本地文件退路
（`LocalFileSource` + `POST /import-csv`，不归档），供本地开发与 CI。配好后用
`uv run python scripts/check_oss.py` 验证整条链路——它按前端直传的真实顺序跑一遍
（STS 签发 → 临时凭证直传 → **越权写入必须被拒** → 服务端流式读回 → bucket CORS），
退出码非 0 即有项未通过。其中"越权被拒"与 CORS 两项无法靠 code review 确认，只能实测。

**bucket 的 CORS 不在本仓库配置范围内，但直传依赖它**：来源需含前端域名、方法需含
`PUT` 与 `POST`（分片上传的 Initiate/Complete 走 POST）、**暴露头必须含 `ETag`**——
ali-oss 要读每片的 ETag 才能 `CompleteMultipartUpload`，缺它的表现是字节全传完了、最后
一步失败，极易误判成网络问题。

**上线前必改**（开发环境放宽处，清单与理由见 `.env.example` 的 OSS 段）：CORS 来源从 `*`
收窄到实际域名、`OSS_ENDPOINT` 换内网地址、生产用独立 bucket 与独立 RAM 子账号。V2 生产
已就位的是独立 bucket（`keywordiq-release`，杭州）与服务端内网 Endpoint，并已用
`scripts/check_oss.py` 全链路验证；**CORS 来源是否已收窄、是否用了独立 RAM 子账号，交接文档
没有记录**，下次动 OSS 配置时要逐项核对，别当成已完成。

**导入串行**：`pg_try_advisory_lock` 保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级导入并发会争抢
WAL 与索引构建），拿不到锁的批次置 `QUEUED`。

**`QUEUED` 必须真的会被接手**，否则它就是个死标签。接手者是 `app/table/upload/import_queue.py`
的轮询任务（lifespan 里起，间隔 `IMPORT_QUEUE_POLL_SECONDS`，默认 30 秒；多 worker 各起一个，
靠同一把咨询锁保证仍是串行）。历史上没有它：日报 300K 行要跑约 4 分钟，这期间提交的第二个文件
停在 `QUEUED` 永不开始，界面显示「队列中」，没有任何东西会回头看它——用户在等一件不会发生的事。

因此两条约束：

- **接手用轮询，不用「导入结束时接着跑下一个」**。链式接力在进程被杀、导入崩在半路、或前一个
  导入由别的 worker 执行时都会断链，而断链的表现恰恰就是「永远排队」，与它要修的病一模一样。
- **重建不出数据源的批次不许留在 `QUEUED`**。本地退路（`OSS_ENABLED=false`）没有 object_key，
  批次里只有文件名，接手时无从重建源——这类当场判 `FAILED` 并写明「请稍后重新上传」。
  留在 `QUEUED` 等于撒谎。

**`UPLOADING` 适用同一条纪律**（`import_service.reap_stale_uploads`，挂在同一个轮询任务上）。
回收的判据不是「多久算久」，而是**这次上传还有没有可能传完**：直传每个请求都由 STS 凭证签名，
凭证一过期后续分片一律被拒。阈值因此由 `OSS_STS_DURATION_SECONDS` 推导（`+300` 秒宽限，
`config.upload_stale_seconds`），**不是独立配置项**——两个旋钮迟早矛盾，而短于凭证有效期会把
正在好好传的 1.5GB 周报判死。超时后按对象是否真在 OSS 分流：

- **在** → 字节传完了、只是没触发导入（关标签页的典型形态）：置 `QUEUED` 交给上面那个接手者，
  **不让用户重传**。这条路径**必须重新执行大小上限**——上限本来在 `/oss/import` 里执行，
  而这里绕过了它，不补就等于开了一条「不调 /oss/import 即可绕过上限」的路。
- **不在** → 判 `FAILED`，写明请重新上传。

**导入批次有两个读取出口，职责不同、不要合并**：上传屏的「最近导入状态」看板
（`GET /processing-status`，固定最近 5 条 + 前端 5 秒轮询）回答「我刚传的文件跑到哪了」；
导入历史屏（`GET /batches`，ProTable 契约 + 站点/周期/状态三项筛选 + 分页，`per_page` 上限
100 且**超限拒绝不截断**）回答「上周 DE 的周报到底导没导」。历史屏**不轮询**——一张带筛选
的表每 5 秒整体刷新，用户翻到第 3 页会被刷回第 1 页。两处查询共用 `import_batch_crud`
的同一份组装代码。

**重导**（`POST /batches/{id}/retry`，权限点同为 `upload:import`）凭批次留存的 object key
重新导入，不必重传 GB 级文件。两条约束：

- **只允许 `FAILED`。** 重导会先删该 (站点, 周期, 期次) 的现有切片再重写；对 `COMPLETED`
  做这件事等于让一份正在被查询的数据先消失、再花几分钟重建。也正因只允许 FAILED（失败
  已整体回滚、切片本就是空的），界面不做二次确认——若将来放开 COMPLETED，必须同时加回确认。
- **重导新建批次记录，不改写原记录。** 原记录的状态与错误信息是排查依据，抹掉它历史上
  就只剩一条看不出曾经失败过的成功记录。重导没有独立执行路径，就是拿同一份归属再调一次
  `run_import`，因此同样受咨询锁串行化、拿不到锁进 `QUEUED` 由轮询任务接手。

准入判定（不存在 404 / 非 FAILED 409 / `object_key` 空 422 / 前缀越权 400 / 对象已删或超限）
**必须全部在触发后台任务之前完成**——全局处理器不拦 `BackgroundTasks`，校验放进后台任务
用户只会看到「已触发」然后什么也不发生。越权前缀与对象称量两道校验与 `/oss/import` 共用
`oss_client.verify_source_object`：重导若少掉越权那道，它就成了绕过授权前缀触发任意切片
导入的入口。

> V1 的 `csv_processor.py`（pandas 分块 + `ON CONFLICT (keyword)` UPSERT + `ranking_trend_day` JSONB
> 7 天走势）随 V1 `/upload-csv` 端点一同退役，不要在 V2 路径复用。
