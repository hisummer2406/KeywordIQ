> 进度记录：2026-11-11 Claude（Opus）认领 `change/add-opportunity-ip-agent`，worktree `~/.claude-worktrees/add-opportunity-ip-agent`。MCP 行为已实测（design Context）；四件套已写。2026-11-11 后端（1–4 节）与前端（第 5 节）代码完成，未提交。验证见 6.1、6.2；前端 lint、tsc、vitest 未运行（按用户约定由用户或提交门禁运行）。

## 1. 契约与配置

- [x] 1.1 `app/table/opportunity/mcp_contract.md`：端点、四个白名单工具、结果形状、已实测的检索式语法、错误分类（design 决策 3；spec「MCP 客户端固定端点并只开放白名单工具」）
- [x] 1.2 `config.py` 与 `.env.example`：`OPPORTUNITY_IP_*` 预算、轮询、租约、超时，`OPPORTUNITY_LLM_*` 四项（决策 2、4、5）

## 2. 数据模型与迁移

- [x] 2.1 `app/table/opportunity/ip_model.py`：`opp_ip_research`、`opp_ip_task`、`opp_ip_call`、`opp_ip_query`、`opp_ip_candidate`、`opp_ip_detail`、`opp_ip_agent_output`、`opp_ip_review`（决策 5、6、8）
- [x] 2.2 登记 `migration_scope.V2_TABLES` 与 `alembic/env.py`
- [x] 2.3 手写迁移；在一次性库上升级、降级、再升级，并核对模型与库无差异（`3b918ff1ccc9`，一次性库 `keywordiq_test_ip_agent`，`alembic check` 无差异）；同时登记 `scripts/verify_selfhosted_migration.py` 核对清单

## 3. 外部客户端

- [x] 3.1 `mcp_client.py`：固定端点、白名单、SSE/JSON 解析、结果分类、`trust_env=False`、日志不含 Key（决策 3）
- [x] 3.2 `llm_client.py`：OpenAI 兼容、仅 https 基址、JSON 输出、错误分类（决策 2）
- [x] 3.3 用 `httpx.MockTransport` 测两个客户端的请求形状与各类结果

## 4. 业务服务与任务

- [x] 4.1 `app/table/risk/review_rules.py` 抽出人工意见校验，#24 `add_review` 改用它（决策 8）
- [x] 4.2 `agent_prompts.py`：两步提示词与输出校验（决策 7）
- [x] 4.3 `research_service.py`：创建、列表、详情、付费动作入队（预算咨询锁、幂等、在途互斥）、人工意见、附图读取（决策 4、8、9）
- [x] 4.4 `task_service.py`：认领、执行（不持有会话）、收尾、租约回收；附图下载（决策 5、6）
- [x] 4.5 `ip_api.py` 挂到 `/opportunity/ip`；`main.py` 启动独立轮询
- [x] 4.6 真库集成测试：私有 404、预算超限、幂等与 409、全流程（假客户端）、失败/未知/无命中区分、取证不重复、租约过期、意见规则（`test_opportunity_ip_integration` 15 项，真库通过）

## 5. 前端

- [x] 5.1 `services/opportunityIp.ts`
- [x] 5.2 「风险待核查」Tab：IP 工作台（列表、新建、详情：检索方案、确认执行、候选与取证、Agent 分析、人工意见、调用与预算）（决策 10）
- [x] 5.3 页首说明与其余 Tab 不发请求保持不变

## 6. 验证与交付

- [ ] 6.1 `uv run ruff check .`、`uv run pytest test/ -q`（真库）、`openspec validate --strict --all`
  - 已跑：ruff 全仓通过；定向 pytest（新增 40 项 + #24 风险、迁移清单、约定、错误契约、RBAC、数据模型等）在一次性库通过；`openspec validate --strict --all` 44 项通过。
  - 未跑：全量 `pytest test/`（提交门禁会跑）；前端 lint、tsc、vitest。
- [ ] 6.2 真实联调：MCP 最小样本走一遍应用内流程；大模型待 Key 到位后联调
  - 2026-11-11 MCP 已通：应用内检索 1 次（10 条候选）+ 取证 1 件 × 3 工具全部成功，附图 9.7 KB 存库可读，预算计 4 次。
  - 2026-11-11 大模型已通（DeepSeek `deepseek-v4-pro`，`https://api.deepseek.com`）：应用内 计划 → 执行 3 条检索式 → 取证 2 件 × 2 工具 → 分析 全流程成功；分析只引用本调研候选、未给上架结论。计划约 4.7k tokens、分析约 4.2k tokens。
  - 联调发现并修复：零命中时供应商返回 `data: {}`，原先被记为「结果未知」；现规范为零命中（`mcp_client.py`，单元测试 `test_mcp_empty_data_object_is_zero_hits`），真实调用复核通过。
- [ ] 6.3 浏览器验收清单交给用户（超管账号）

## 7. 工作空间 + 对话布局（2026-11-11 用户追加，参考 WorkBuddy；决策 10–14）

- [x] 7.1 规格：opportunity-center、top-nav-structure 的 MODIFIED，opportunity-ip-agent 新增工作空间、追问、定时复检三条；`docs/opportunity-center/00-decision-summary.md` 记录改定
- [x] 7.2 模型与迁移：`opp_workspace`、`opp_ip_message`、`opp_ip_schedule`，`opp_ip_research.workspace_id`，任务 kind 加 `ask`（并进未发布的 `3b918ff1ccc9`；一次性库重建后升级、降级、再升级，`alembic check` 无差异）；登记 V2_TABLES 与迁移核对清单
- [x] 7.3 服务与 API：工作空间增删改查与移动（`/opportunity/workspaces`）、追问任务（`POST .../messages`）、定时复检（`GET /schedules`、`PUT|DELETE .../schedule`）、到期处理并入 IP 轮询器
- [x] 7.4 测试：真库集成 7 项（工作空间、追问、定时复检）、`next_run` 单元、机会中心全部路由挂 `opportunity:view` 的单元测试
- [x] 7.5 前端：页内左栏（新建任务 / 专家 / 定时任务 / 工作空间与任务）、任务对话（消息流 + 待确认检索式卡片 + 追问输入框）、成果抽屉、定时复检弹窗、专家名片、定时任务列表；消息流拼装 `timeline.ts` 及其 vitest
- [x] 7.6 真实联调（DeepSeek + 智慧芽，一次性库）：建工作空间 → 发起任务 → 方案 → 执行 1 条检索 → 追问 → 设定时复检并提前触发到期 → 定时检索入队并执行，全部成功（MCP 共 2 次；方案约 2.2k、追问约 2.8k tokens）。发现回答正文用内部 id 称呼候选，已改提示词要求用公开号，真实追问复核通过
  - 观察到：本会话 DeepSeek 共 4 次调用，有 1 次在 15.9 秒时被对端断开（超时设 120 秒），记为结果未知、不自动重发，用户可在对话里重试；上线后需观察频率
- [x] 7.7 前端 `pnpm tsc`、`pnpm lint`、`pnpm test` 通过（vitest 59 个文件 630 项，含 timeline 5 项）；lint 发现 `ipLabels.ts` 先用后定义，已调整顺序
- [ ] 7.8 浏览器验收（超管账号）
