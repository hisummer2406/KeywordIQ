> 进度记录：2026-11-12 Claude（Opus 5.5）认领 `change/add-opportunity-feasibility`，worktree `~/.claude-worktrees/add-opportunity-feasibility`（基于 v2 `fa0556b`，不含未合并的 PR #188）。用户授权「商业可行性专家，继续工作」；合并与发布另行授权。

## 1. 数据与后端

- [x] 1.1 迁移 `66afc8db887d`：`pricing_sheet.dossier_id`（FK SET NULL + 部分索引）、`opp_dossier_decision.pricing_status`；模型同步。一次性库 `keywordiq_test_feasibility` 上升级、降级、再升级，`alembic check` 无差异（design 决策 1、4）
- [x] 1.2 `dossier_pricing.py`：`pricing_summary`（字段白名单，选定 → 推荐 → 第一个可算方案，旧版本选定提示，市场不可算时不给方案）、`status_for_decision`、关联/解除（只认自己的核价单，404）、我的可关联核价单、从档案建草稿；`dossier_service` 详情带摘要、决定固化 `pricing_status`（决策 2、3、4）
- [x] 1.3 API：`PUT/DELETE /dossiers/{id}/pricing-sheets/{sheet_id}`、`GET /dossiers/linkable-pricing-sheets`（`opportunity:view`）、`POST /dossiers/{id}/pricing-sheets`（另挂 `pricing:view`）
- [x] 1.4 追问输入加测算摘要与三条说明（私有输入已计入、采购价上限只在倒推方式下计算、利润率是小数），提示词第 6 条：金额只原样引用（决策 5）
- [x] 1.5 真库集成测试 `test_opportunity_feasibility_integration.py` 7 项，走真实核价引擎（事务内启用最小 US 规则）：摘要取引擎原值、私有字段不外泄、他人 404、选定/推荐/旧版本选定、配置缺失不给金额、只有草稿、关联与解除、删除核价单不影响档案、从档案建草稿、决定固化不随新版本变化、追问输入与路由权限

## 2. 前端

- [x] 2.1 `PricingPanel.tsx`：摘要表格（方案、选定状态、售价、利润、利润率、保本价、采购价上限）、规则已更新提示、配置缺失说明、关联、发起核价（跳 `/pricing/calculate?sheet=`，核价页不改）；`dossierView.ts` 的 `formatMoney`/`formatRate` 只做展示（vitest 覆盖）
- [x] 2.2 决定记录与签署提示展示测算状态
- [ ] 2.3 PR #188 合并后：专家名片「商业可行性」改为已接入（本分支基于不含 #188 的 v2，避免名片文件冲突，留到 #188 合并后补）

## 3. 验证与交付

- [x] 3.1 `uv run ruff check .` 通过；定向 pytest 173 项（feasibility、dossier、pricing 集成，IP 单元，data model，conventions）通过；全量由提交门禁运行；`openspec validate --strict --all` 通过
- [x] 3.2 前端 `pnpm tsc`、`pnpm lint` 通过；`pnpm test` 60 个文件 643 项通过
- [x] 3.3 演示库（启用一套标注为「演示构造规则」的 US 规则，非真实费率）经 HTTP：yunying2 从「迷你无线直发梳」档案发起核价 → 保存版本并选定带电快线 → yunying1 在档案看到售价 $17.36、利润 ¥25.05、利润率、保本价，打开核价单明细 404 → 签补证，决定固化测算。追问用 DeepSeek 真实调用 3 次：第 1 次被另一个旧代码演示服务抢先认领（演示环境两套服务共用一个库，非代码问题）；第 2 次正确引用引擎原值，但把「私有输入没展示」说成缺数据、误解采购价上限为空的原因；补了三条说明后第 3 次回答正确
- [ ] 3.4 PR 回 v2；浏览器验收清单交给用户（实现方没有做浏览器实测）

## 归档顺序

本变更的 spec delta 修改 `opportunity-dossier`，该能力由尚未归档的 `add-opportunity-dossier` 新增：必须先归档 `add-opportunity-dossier`，再归档本变更。
