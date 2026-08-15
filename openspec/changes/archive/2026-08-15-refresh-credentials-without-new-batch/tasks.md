## 1. 后端：区分续签与新上传

- [x] 1.1 `upload_schemas.py`：`UploadCredentialRequest` 增可选 `object_key`
- [x] 1.2 `oss_client.py`：按已有 key 签发凭证的入口（不生成新 key）；
      前缀校验复用 `/oss/import` 与重导那一份，但**不**做对象存在性与大小校验
      （续签时对象尚未 Complete，必然查不到）
- [x] 1.3 `upload_api.py`：带 `object_key` 走续签分支——只签发、不落库、不生成新 key
- [x] 1.4 不带 `object_key` 的行为**逐字不变**（含落库失败则不签发）

## 2. 前端：续签带上既有 key

- [x] 2.1 `services/upload.ts`：`getUploadCredentials` 透传可选 `object_key`
- [x] 2.2 `services/ossUpload.ts`：`refreshSTSToken` 传 `cred.object_key`
- [x] 2.3 删掉"必须丢弃续签返回的新 key"那段注释与相应处理——续签已不再产生新 key，
      留着会误导下一个读者

## 3. 测试

- [x] 3.1 `test_upload_credentials_api_unit.py`：续签**不**落批次记录
- [x] 3.2 同上：新上传仍落记录（既有行为不回退）
- [x] 3.3 同上：续签的 key 越权前缀被拒
- [x] 3.4 同上：续签不要求对象存在
- [x] 3.5 `ossUpload.test.ts`：续签回调带上 object_key，且与首次是同一个 key
- [x] 3.6 `uv run pytest test/ -q` 与 `pnpm lint && pnpm tsc && pnpm test` 全绿

## 4. 实机核对

- [ ] 4.1 传一份周报（需时长于 5 分钟以触发至少一次续签），确认库中该次上传只有一条记录
- [ ] 4.2 确认续签期间上传不中断（凭证确实生效）

## 5. 遗留数据（不在代码改动内）

- [ ] 5.1 人工核对并清除已产生的幽灵 `UPLOADING` 记录。**注意**：同一 (site, period,
      report_date) 下最早的那条通常是真实记录，其余为幽灵；勿连真实记录一并删除
