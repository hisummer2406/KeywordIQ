> 进度记录：2026-11-12 Claude（Opus）认领 `change/add-patent-fulltext-evidence`，worktree `~/.claude-worktrees/add-patent-fulltext-evidence`。Refs #24。
> 2026-11-12：1–5 节已实现。一次性库 `keywordiq_test_fulltext` 上迁移升级、降级、再升级，`alembic check` 无差异；`test_risk_check_integration`（真库）、`test_risk_provider_client_unit`、`test_risk_api_unit`、`test_risk_evidence_store_unit` 共 88 passed、0 skipped；ruff 通过。前端 lint、tsc、vitest 由提交门禁运行。6.2 待用户授权。

## 1. 契约与配置

- [x] 1.1 `provider_contract.md` 补 P042、P020 契约与 2026-11-11 联调样本形状；记下未验证项（P042 `limit` 上限、P020 批量、计费、QPS）（provider「客户端固定源站并按契约调用接口」）
- [x] 1.2 `config.py` 与 `.env.example` 新增 `RISK_EVIDENCE_OSS_PREFIX`（默认 `risk-evidence`）（design 决策 2）

## 2. 数据模型与迁移

- [x] 2.1 `DETAIL_KINDS` 增加 `drawings`、`pdf`；手写迁移替换 `ck_risk_patent_detail_kind`；在一次性库上升级、降级、再升级，并检查模型差异（决策 3）

## 3. 供应商与存档

- [x] 3.1 `provider_client.py`：`fulltext_image`（P042）、`pdf_data`（P020）及结构校验；下载放在 `evidence_store.archive_file`（主机白名单、不跟随重定向、不读代理、流式上限、文件头判断）（provider「签名文件只从智慧芽文件域名下载」）
- [x] 3.2 `evidence_store.py`：OSS 写入与读取（线程池），key 规则，OSS 未启用时返回仅链接（check「附图与公报存档」）
- [x] 3.3 模拟 HTTP 服务测试：请求形状、成功、无数据、错误码、非可信主机、重定向、超限、格式不符

## 4. 任务与服务

- [x] 4.1 `task_service`：按件执行 P042（翻页）与 P020，下载并存档；部分翻页失败按失败定性；`renew` 续租（check「专利详情按需获取」、决策 1、2、5）
- [x] 4.2 `check_service`：文件种类的批量上限 10；`get_check` 剥掉 object_key 与不可信链接，给出可读标记；`get_evidence_file` 归属校验后从 OSS 读（决策 3、4）
- [x] 4.3 `risk_api`：`GET /risk/checks/{id}/files/{candidate_id}/{kind}/{seq}`；`meta` 增加 `max_file_detail_batch`、`evidence_archive_enabled`
- [x] 4.4 集成测试（真库）：附图翻页成功并存档、OSS 未启用仅链接、翻页中途失败、超限 422、他人读文件 404、成功后不重复调用、续租生效

## 5. 前端

- [x] 5.1 `services/risk.ts`：新种类、文件读取（Blob）、meta 字段
- [x] 5.2 `CandidateTable`：种类选择与调用说明、四种状态标签、附图图集与公报按钮、未存档提示、并排比对可选存档附图
- [x] 5.3 前端单测覆盖种类选择的批量上限与调用说明文案（`riskLabels.test.ts`）

## 6. 验证与交付

- [ ] 6.1 后端定向测试与 ruff；`openspec validate --strict --all`；前端 lint、tsc、vitest 由提交门禁运行
- [ ] 6.2 上线前实测（需用户授权、产生计费）：生产 OSS 子账号对 `risk-evidence/` 的写读权限与生命周期规则；P042 `limit=20` 与附图多于 5 张的专利；浏览器验收
