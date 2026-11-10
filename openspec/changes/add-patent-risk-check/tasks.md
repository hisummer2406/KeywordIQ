> 进度记录：2026-11-10 Claude（Opus）认领 `change/add-patent-risk-check`，worktree `~/.claude-worktrees/add-patent-risk-check`；四件套已写，2026-11-10 后端（1–5 节）完成：一次性库 `keywordiq_test_patent` 上迁移升级、降级、再升级通过且无模型差异；`test_risk_provider_client_unit`（22）、`test_risk_api_unit`（9）、`test_risk_check_integration`（17，真库）通过。前端待做。

## 1. 契约与配置

- [x] 1.1 写 `app/table/risk/provider_contract.md`：四个接口的精简契约、错误码表、已知冲突（`country` 类型、附图过期时间），以及脱敏的成功、无命中、错误响应样例（design 决策 4、5；provider「客户端固定源站并按契约调用」）
- [x] 1.2 `config.py` 新增 `ZHIHUIYA_API_KEY`、`RISK_TASK_POLL_SECONDS`、`RISK_TASK_LEASE_SECONDS`、`RISK_PROVIDER_TIMEOUT_SECONDS`，同步 `.env.example`（单位、安全默认值）（决策 1、3）

## 2. 数据模型与迁移

- [x] 2.1 `app/table/risk/risk_model.py`：`risk_check`、`risk_check_image`、`risk_task`、`risk_provider_call`、`risk_search_page`、`risk_candidate`、`risk_patent_detail`、`risk_review`，以及唯一约束和索引（决策 2、3、6、7、8、10）
- [x] 2.2 登记 `migration_scope.V2_TABLES`（普通表，不登记分区保留）
- [x] 2.3 `app/auth/permissions.py` 新增 `risk:view`、`risk:search`、`risk:review`（admin 默认获得，不放进 super-only）（决策 9）
- [x] 2.4 手写 Alembic 迁移：建表加权限种子；在一次性库上升级、降级、再升级，并检查模型与库的差异

## 3. 供应商层

- [x] 3.2 `provider_client.py`：httpx AsyncClient 固定 base_url，实现 P010/P060/P011/P013，响应结构校验，错误分类，日志不含鉴权头（决策 4）
- [x] 3.3 用本地模拟 HTTP 服务测试真实客户端：请求头、multipart、P060 参数、批量 `patent_id`；以及成功、无命中、无权限、余额不足、429、超时、502 HTML、结构异常

## 4. 业务服务与任务

- [x] 4.2 `check_service.py`：创建核查单（站点和法域校验、图片魔数和大小、输入指纹幂等、previous 归属）、列表与详情（归属 404）、读取原图
- [x] 4.3 `task_service.py`：`SKIP LOCKED` 认领与租约、执行 search/page/detail（调用期间不持有会话）、写入证据、回收过期租约为 unknown、单核查单检索互斥（决策 3、6、7、8）
- [x] 4.4 `review_service.py`：追加意见与全部校验规则（review 规格）
- [x] 4.5 `main.py` 启动独立的风险任务轮询

## 5. API

- [x] 5.1 `risk_api.py`：`/api/risk/meta`、`/checks`（POST 202、GET 分页）、`/checks/{id}`、`/image`、`/pages`、`/evidence`、`/reviews`、`/retry`；逐端点鉴权；挂到 `api_router`
- [x] 5.2 集成测试（真实数据库）：归属 404、权限 403、幂等与 409、并发认领只执行一次、租约过期转 unknown 不重发、详情成功项不重复调用、意见校验、未配置 Key 时零外部调用

## 6. 前端

- [ ] 6.1 `services/risk`；`access.ts` 新增 `canRiskView/Search/Review`；`routes.ts` 改为 risk:view 把守，新增 `/ip/review/:id`，`/ip` 重定向到审核待办（top-nav delta）
- [ ] 6.3 `IP/DesignPatent`：表单、市场与法域（推荐需勾选、EU 说明）、本地预览、调用说明、令牌防重复、未配置时的提示
- [ ] 6.4 `IP/Review` 列表与 `IP/ReviewDetail`：状态轮询、读取范围、候选与并排比对、下一页和详情按钮的调用提示、安全渲染与 https 校验、分市场意见表单和历史、前后轮次链接、新一轮
- [ ] 6.5 前端单元测试：路由和权限、表单校验、无命中和未知状态的文案、意见规则

## 7. 验证与交付

- [ ] 7.1 后端 `uv run ruff check .`、`uv run pytest test/ -q`（含一次性库上的集成测试）
- [ ] 7.2 前端 `pnpm lint && pnpm tsc && pnpm test`（按用户约定由用户跑或门禁跑，报告实际情况）
- [ ] 7.3 `openspec validate --strict --all`、`git diff --check`
- [ ] 7.4 开 PR（`--base v2`，Refs #24），分别列明：模拟联调通过、真实数据库通过、浏览器待验收、真实 Key 待联调
- [ ] 7.5 （待用户配置 Key 后）最小样本真实联调：先报告调用项和预期消耗；核实 `country` 传法、EU/WO 取值、权益、计费方式与 QPS
