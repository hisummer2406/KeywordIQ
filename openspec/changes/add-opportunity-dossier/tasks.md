> 进度记录：2026-11-12 Claude（Fable 5.2）认领 `change/add-opportunity-dossier`，worktree `~/.claude-worktrees/add-opportunity-dossier`。用户授权「按你的理解直接推进到运营可用的版本」；合并与发布另行授权。三个真实案例的生产只读核查见 proposal。2026-11-12 三轮代码完成；验证见第 4 节；浏览器验收未做（4.3）。

## 1. 第一轮：从一组词到签署决定（spec「机会档案…团队可见」「ABA 证据…」「未接入的数据…」「决定只追加…」「复查时…」「档案关联专利调研…」）

- [x] 1.1 模型与迁移：`dossier_model.py` 五张表，`opp_ip_research.dossier_id`；登记 `migration_scope.V2_TABLES`、`alembic/env.py` 与 `scripts/verify_selfhosted_migration.py`；迁移 `4b219decb93a`（design 决策 1、3、5、7）。一次性库 `keywordiq_test_dossier` 上升级、降级、再升级，`alembic check` 无差异
- [x] 1.2 权限：`OPPORTUNITY_VIEW` 移出 `SUPER_ONLY_PERMISSIONS` 并授予三个内置角色（同一条迁移补 `role_permission`）；IP 专家三个计费端点另挂 `risk:search`（`ip_api.py`）；`test_rbac_auth_unit`、`test_opportunity_ip_client_unit::test_paid_ip_routes_also_require_risk_search`（决策 8；opportunity-center「权限点」「计费的专利检索不随机会中心权限铺开」）
- [x] 1.3 `dossier_evidence.py`：ABA 快照计算与快照对比，复用 `trend_service.get_keyword_trend` 与 `fact_metric_columns`（决策 3）
- [x] 1.4 `dossier_service.py`：建档（含从词库分组）、列表、详情、改信息、接手、归档、增删关键词、刷新快照、人工证据与作废、决定、关联 IP 调研（决策 2、4、5、6）
- [x] 1.5 `dossier_api.py` 挂 `/opportunity/dossiers`，每个端点挂 `opportunity:view`（既有 `test_every_opportunity_route_requires_opportunity_view` 覆盖新路由）
- [x] 1.6 真库集成测试 `test_opportunity_dossier_integration.py`：建档与未解析词、断续在榜与不足 7 期、重复刷新不追加、迟到解析、决定规则、复查变化、团队可见与接手、IP 摘要可见而明细 404、关联他人调研 404
- [x] 1.7 前端：`services/opportunityDossier.ts`；左栏「机会档案」与默认视图；`Dossier/` 下列表、新建、详情（证据、缺口、专利、决定、变化）；`dossierView.ts` 及其 vitest（决策 9；opportunity-center「默认入口」）
- [x] 1.8 演示：本地库 `keywordiq_demo_dossier` 灌入生产只读导出的三个案例真实 ABA 行（8 个词，截至 11-09），两个 operator 账号经 HTTP 走通 建档 → 证据 → 人工证据 → 接手 → 决定；再灌入 11-10 一期后刷新，三份档案都给出相对「上次决定所依据快照」的变化

## 2. 第二轮：追问（spec「追问只基于档案内证据…」）

- [x] 2.1 `dossier_ask.py`：入队、认领与租约、输入组装、输出校验、收尾；并入 IP 轮询器（`main.py`）（决策 7）
- [x] 2.2 真库集成测试（假大模型）：成功、失败、未知、在途 409、未配置 422、越界与已作废引用丢弃、租约过期转 unknown 且迟到结果不覆盖、提示词含禁止估算与未核查约束
- [x] 2.3 前端：`AskPanel.tsx`（在途每 3 秒轮询、引用证据标签、失败与未知分开）
- [x] 2.4 真实联调（DeepSeek `deepseek-v4-pro`，演示库）：对「迷你无线直发梳」问「头部稳定吗？月销量大概多少？能不能推进？」，1 次调用成功；回答引用了 ABA 快照，明确说销量未接入、无法估算，并指出专利未核查。联调后按观察调整了两处：churn 字段改名（模型曾把「在榜期数」读成「点击第一出现期数」）、提示词明确被问「能不能推进」时把决定留给用户；调整后未再做真实调用

## 3. 第三轮：入口

- [x] 3.1 「我的词库」分组菜单「用这个分组建机会档案」，跳到 `/opportunity?view=new-dossier&group=<id>`；无 `opportunity:view` 时不出现
- [x] 3.2 档案里「发起 IP 调研」带入标题、想法、关键词与站点，创建后自动关联回档案；无 `risk:search` 时在发起处提示

## 4. 验证与交付

- [x] 4.1 后端：`uv run ruff check .` 通过；定向 `pytest`（一次性库，首次运行）224 项通过：dossier 集成 20 项、IP 单元与集成、RBAC、risk api、conventions、data model、迁移核对、database；`openspec validate --strict --all` 46 项通过。未跑全量 `pytest test/`（提交门禁与 CI 会跑）
- [x] 4.2 前端：`pnpm tsc`、`pnpm lint` 通过；`pnpm test` 60 个文件 640 项通过（含 `dossierView.test.ts`）
- [ ] 4.3 浏览器验收（清单见 PR 说明；本会话没有做浏览器实测）
- [ ] 4.4 PR 回 v2（合并、发布另行授权）

## 未做（留给后续变更）

- 定价核算与档案的自动关联（目前用「供应成本」「售价」人工证据承接）
- 从市场分析行、增长榜直接建档（目前入口是词库分组与手动填写）
- 档案内 IP 调研明细的团队共享（目前只共享摘要）
- 追问的真实样例评测集
