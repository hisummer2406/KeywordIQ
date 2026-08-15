## Why

`POST /oss/upload-credentials` 被两种调用者共用，而它们对「落一条 `UPLOADING` 批次」这个
副作用的需求正相反：

- **开始一次新上传**（`ossUpload.ts` 第 58 行）——需要落库。没有这条记录，用户关掉标签页
  就等于什么都没发生过。
- **给正在进行的上传续凭证**（`ossUpload.ts` 第 72-79 行的 `refreshSTSToken` 回调，每
  5 分钟一次）——**不该落库**。上传还是那一次，object key 还是那一个。

两者先后独立落地，各自都对：续期在 2026-08-01（`dd7fb40`），落库副作用在 2026-08-12
（`a0e8198`）。后者没有回头看这个端点还被谁调用，于是**每一次续期都被当成一次全新上传**。

实测（本地 dev 库，4 个周报文件并行直传）：同一个文件在 `07:06` 落下真实批次后，于
`07:11 / 07:16 / 07:21 / 07:26` 各多出一条 `UPLOADING` 幽灵记录，间隔与
`STS_REFRESH_INTERVAL_MS = 5 分钟` 精确吻合。一次上传 4 个文件、传 25 分钟，
导入历史里就多出 16 条不存在的"上传"。

**危害不止于记录变多。** 幽灵记录的 object key 从未被写入过任何对象，回收任务
（`reap_stale_uploads`，阈值 `OSS_STS_DURATION_SECONDS + 300` = 65 分钟）到点后去 OSS
查不到对象，会把它们判成 `FAILED`「请重新上传」——**文件明明传成功了，用户却看到一堆
失败记录**。这比没有记录更糟：它主动给出了错误的结论。

本变更影响 OSS 直传路径，日报与周报同等适用；周报（1.5GB、耗时长、续期次数多）受害更重。

## What Changes

- 续期请求 SHALL 携带本次上传已有的 `object_key`；后端据此识别出"这是续期而非新上传"，
  **只签发凭证、不落批次记录、不生成新 key**
- 携带的 `object_key` 仍 SHALL 走与 `/oss/import`、重导相同的授权前缀校验——续期不能
  成为绕过前缀限制的入口
- 前端 `refreshSTSToken` 回调改为传入 `cred.object_key`；不再丢弃"新签发的 key"，
  因为续期路径不再产生新 key
- **BREAKING**：无。请求体新增可选字段，旧调用方（不传 `object_key`）行为不变

## Capabilities

### New Capabilities

无。修的是既有端点的一个副作用边界。

### Modified Capabilities

- `oss-first-ingestion`: 签发凭证的要求需区分「新上传」与「续期」两种调用，
  只有前者落 `UPLOADING` 批次。

## Impact

| 文件 | 改动 |
|---|---|
| `app/table/upload/upload_schemas.py` | `UploadCredentialRequest` 增可选 `object_key` |
| `app/table/upload/upload_api.py` | 续期分支：校验前缀 → 只签发，不落库 |
| `app/table/upload/oss_client.py` | 按已有 key 签发凭证（不生成新 key）的入口 |
| `frontend/src/services/upload.ts` | `getUploadCredentials` 透传 `object_key` |
| `frontend/src/services/ossUpload.ts` | `refreshSTSToken` 带上 `cred.object_key` |
| `test/test_upload_credentials_api_unit.py` | 续期不落库、续期仍校验前缀 |
| `frontend/src/services/ossUpload.test.ts` | 续期回调带 object_key |

**遗留数据**：本次改动不追溯清理已产生的幽灵 `UPLOADING` 记录。它们会在 65 分钟后被
回收判为 `FAILED`，需人工核对后清除（见 tasks.md）。
