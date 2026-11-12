## Context

- 上一变更 `2026-11-10-add-patent-risk-check` 的决策 3（持久化任务、调用期间不持有会话）、决策 4（调用留痕）、决策 7（详情按需、成功不重复取）原样沿用。
- 2026-11-11 接口级真实联调（样本 `USD976055S`）：
  - P042 `POST /basic-patent-data/fulltext-image`，JSON 请求体 `{patent_id, limit, offset}`，一次一件专利。`data` 是对象：`patent_id`、`pn`、`total_search_result_count`（该件附图总数，样本为 8）、`result_count`（本页条数）、`fulltext_image[]`（每条 `image_type`、`path`）。`path` 是 `https://data-fulltext-image.zhihuiya.com/...png?sign=...&response-expires=...`，约 7 天后过期。
  - P020 `GET /basic-patent-data/pdf-data?patent_id=...`，`data` 是数组，每条 `{patent_id, pn, pdf: {path}}`。`path` 位于 `https://data-pdf.zhihuiya.com/...`，约 10 天后过期。
  - 未验证：P042 的 `limit` 上限（实测只用过 5）、P020 是否支持逗号批量、两者的计费方式与 QPS。
- 用户决策（2026-11-12）：用户手动获取的附图和公报存档到 OSS（「存档，如果有文件就保存到 oss」）。

## Goals / Non-Goals

**Goals:** 已获取的附图与公报长期可读；不因存档失败而重复计费；获取多件时不被租约误判为未知。

**Non-Goals:** 不镜像检索命中图（P060 的 `url`）；不提供「只重试存档」的单独操作（链接有效期内仍可直接打开）；不做存档文件的删除和生命周期管理（核查记录本身也没有删除功能）。

## Decisions

### 决策 1：沿用「补充详情」，扩展为四种

- `DETAIL_KINDS` = `bibliography`、`legal`、`drawings`、`pdf`。`EvidenceRequest.kinds` 默认值保持为著录和法律状态，旧客户端行为不变。
- 包含 `drawings` 或 `pdf` 时，一次最多选 10 件（`MAX_FILE_DETAIL_BATCH`）。著录和法律状态仍是 20 件：文件要下载、存档，单个任务耗时随件数线性增长。
- P042 每件单独调用，按 `limit=20` 翻页直到读满 `total_search_result_count`，单件最多 100 张。翻页的每一次都单独留痕。P020 也按件单独调用：批量传法未验证，按件调用能确保一件失败只影响这一件。
- 详情状态的含义不变：接口成功即 `success`（已可能计费，不再重复调用）；接口成功但该件无数据为 `missing`；接口错误为 `failed`，超时或结构异常为 `unknown`。P042 翻页中途失败时，整件按失败的那一次调用定性，已拿到的那几页不落为 `success`，避免把不完整的附图集当成完整证据。

### 决策 2：下载与存档在任务执行阶段完成，失败只降级为「仅链接」

- `_execute` 在接口成功后逐个下载签名链接：只接受 `https://` 且主机名以 `.zhihuiya.com` 结尾的地址，不跟随重定向，不读环境代理；流式读取，图片上限 20 MB、PDF 上限 50 MB；按文件头判断格式（图片接受 PNG/JPEG/GIF/WebP/TIFF，PDF 必须以 `%PDF-` 开头）。
- 下载成功后经线程池调用阻塞的 `oss2.put_object` 写入，key 为 `<RISK_EVIDENCE_OSS_PREFIX>/<check_id>/<candidate_id>/<kind>-<序号>-<sha256 前 12 位>.<扩展名>`。存档成功的条目记录 `object_key`、`content_type`、`size_bytes`、`sha256` 和 `archived_at`。
- OSS 未启用、下载失败或写入失败时，该条目记录签名链接、`link_expires_at`（取自 `response-expires`，没有就按取证时间加 7 天）和 `archive_error` 分类，不抛出，不改变详情状态。日志只记分类，不记完整签名 URL。
- 字节流一件一处理，处理完即释放，不在快照里累积。
- 取舍：存档放在 worker 里同步完成，没有再引入独立的存档队列。现有任务表已经提供认领、租约和留痕，再加一个队列只是为「下载失败后重试」服务，而链接本身还有 7 到 10 天的有效期。

### 决策 3：文件清单放在 `risk_patent_detail.data`，不新建表

- `data.normalized` = `{pn, total, files: [{seq, image_type, archived, content_type, size_bytes, sha256, archived_at, link_expires_at, archive_error}]}`。`object_key` 和签名链接放在同一条目里，但 `get_check` 输出时剥掉，改为给出 `/api/risk/checks/{id}/files/{candidate_id}/{kind}/{seq}` 是否可读，以及仅链接条目的 `link_url`（https，且主机名以 `.zhihuiya.com` 结尾）。
- 迁移只改 `ck_risk_patent_detail_kind`。文件是证据，查询都是按候选读的，不需要独立表上的索引。

### 决策 4：读取经后端代理

- `GET /risk/checks/{id}/files/{candidate_id}/{kind}/{seq}` 需要 `risk:view` 和归属校验（查不到就返回 404），从 OSS 读出后返回，带 `X-Content-Type-Options: nosniff`、`Cache-Control: private`。PDF 用 `inline` 并按公开号命名；图片的 Content-Type 用存档时判断出的类型，不信任供应商的扩展名。
- 前端跟原图一样，带登录凭证取 Blob 后转成对象 URL。不签发 OSS 直链：bucket 保持私有，归属校验只有一处。

### 决策 5：执行期间续租

- `run_one` 给 `_execute` 传一个 `renew` 回调，每处理完一件就开一个短会话，把 `lease_until` 推到「现在 + RISK_TASK_LEASE_SECONDS」（只更新仍是 `running` 的本任务）。续租不持有调用期间的连接。检索任务的调用次数固定，不需要续租。
- 续租本身失败只记日志；最坏情况是租约到期被回收为 `unknown`，与现状一致。

## Risks / Trade-offs

- **生产 OSS 权限未验证**：服务端子账号目前已验证的是读对象与签发 STS；写入 `risk-evidence/` 前缀、bucket 生命周期是否覆盖该前缀，需要在上线前用一次性探针实测。不通过时文件降级为仅链接，功能可用但不存档。
- **P042 翻页参数未验证**：`limit=20` 超出供应商上限时，整件附图都会失败。上线前用一件附图多于 5 张的专利实测一次。
- **调用量**：10 件附图至少 10 次 P042 调用，加 10 次 P020。计费方式未知，界面只说明调用次数，不编造金额。
