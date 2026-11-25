# CLAUDE.md

KeywordIQ 使用 FastAPI、PostgreSQL 和 AntD Pro/Umi Max；依赖分别由 uv、pnpm 管理。
本文件是通用工作指令；Codex 另读根目录 AGENTS.md 的环境适配和专用审核规则。
业务行为以 OpenSpec 为准，代码位置与配置从仓库读取；不在本文件维护功能清单或交付进度。

## 开始工作

- 先看 `git status --short`、当前分支、`openspec list`、`git worktree list` 和已有 PR，保护其他会话改动。
- V2 开发基于主仓 `main`，PR 显式指定 `--base main`；`v1` 归档 V1。
- 局部修复直接做；有关键分叉时一次对齐；新子系统、契约或长期决策使用 OpenSpec 四件套和独立 PR。
- 完整变更先读 [.claude/skills/start-change/SKILL.md](.claude/skills/start-change/SKILL.md)，认领分支并建仓库外 worktree。
- 同一变更已有分支时先核实归属，不另做一份；不要抢端口、停止他人服务或清理他人 worktree。
- 用户已明确的范围和授权继续有效；仅对无法替代的缺失决策提问，必要问题一次问完。

## 验证命令

安装后端依赖：`uv sync`。新增运行依赖用 `uv add`，开发依赖加 `--dev`。
开发中按影响面跑 focused 检查；提交前按下表跑对应完整检查，无新变化不反复重跑。

| 改动 | 提交前运行 |
|---|---|
| app/scripts/test/alembic 或顶层 Python | `uv run ruff check .` 和 `uv run pytest test/ -q` |
| frontend | 在 frontend 下运行 `pnpm lint && pnpm tsc && pnpm test` |
| extension | 在 extension 下运行 `pnpm run typecheck && pnpm run test` |
| 只有文档 | `git diff --check`，检查引用和原约束去向 |
| OpenSpec | 另运行 `openspec validate --strict --all` |

- Claude Code 的提交 hook 不保护其他 Agent；没有自动门禁的环境必须主动执行相应检查。
- 报告实际运行、失败与跳过。单元测试、真实数据库、浏览器、生产和真机验证分别陈述。
- 数据库回归前读 [.claude/skills/local-db/SKILL.md](.claude/skills/local-db/SKILL.md)；改导入路径另读并执行 [verify-import](.claude/skills/verify-import/SKILL.md)。
- `keywordiq_test` 是共享库，全量集成夹具可能清空业务表；不放需要保留的数据，drop 前检查活动连接。
- `.umirc.ts`、vitest 配置和测试 setup 等全局文件变更必须跑前端全量，不能用 related 的零用例当通过。
- 跨列表/数量/导出的改动用 [verify-analysis-contract](.claude/skills/verify-analysis-contract/SKILL.md) 选验证；线上只读定位用 [diagnose-v2](.claude/skills/diagnose-v2/SKILL.md)，不附带修复授权。

## 通用约束

- API 保持薄控制器，业务编排及事务由 service 管，CRUD 不新增独立提交；沿用现有公共实现，避免复制口径。
- 只用 SQLAlchemy 2.0 和 Pydantic v2 API；不恢复已退役的 V1 导入或查询实现。
- 不在异步事件循环执行阻塞工作，不整读 GB 文件，不返回未分页全表；性能分别考虑日/周报规模。
- 新配置同步 `.env.example` 的单位及安全默认值；字段、参数和默认值以代码/配置为来源，文档不要复制一份模板。
- 不提交密钥、`.env`、客户报表、日志或临时数据；错误响应不泄露内部细节。
- 先核实数据、权限和环境前提；缺失、未知、未接入与真 0 分开，不编造结果或验收状态。
- 事实写入、站点、权限、迁移和生产隔离的特殊约束在下表局部规则；改动前必须读取命中项。

## 局部规则与参考入口

Claude Code 按 `.claude/rules/` 的 `paths` 加载规则；不支持该机制的 Agent 按本表手动读取。
修改测试、脚本、文档或跨域逻辑时，按其影响的领域取规则并集；不能仅根据待编辑文件的后缀判断。
这些链接不使用 `@` 全量导入；先读命中规则，再读规则链接的相关 spec/design，不能把所有文档都常驻。

| 影响范围 | 规则 | 需要时查阅的规格/设计导航 |
|---|---|---|
| 后端分层、错误、配置、模型与迁移 | [backend](.claude/rules/backend.md) | [规格目录](openspec/specs/) |
| 数据模型、查询/导出、词库/趋势、首次证据、保留清理 | [analysis](.claude/rules/analysis.md) | [分析](openspec/guides/analysis.md) |
| 导入/重导/OSS/批次 | [ingestion](.claude/rules/ingestion.md) + analysis | [导入](openspec/guides/ingestion.md) |
| 登录/退出、RBAC、令牌及 SSO | [auth](.claude/rules/auth.md) | [认证](openspec/guides/auth.md) |
| 前端状态、缓存、筛选、导航 | [frontend](.claude/rules/frontend.md)；涉及认证时另读 auth | [前端](openspec/guides/frontend.md) |
| 竞对关注、上报、抓取、上新流 | [competitor](.claude/rules/competitor.md) | [竞对](openspec/guides/competitor.md) |
| 插件采集、权限及打包 | [extension](.claude/rules/extension.md) + competitor | [插件说明](extension/README.md) |
| 生产、发布、池/锁、OSS/飞书运维 | [operations](.claude/rules/operations.md) | [运维入口](openspec/guides/operations.md) |
| 规格、交付、规则文件及文档维护 | [workflow](.claude/rules/workflow.md) | [操作入口](openspec/guides/workflow.md) |

## 提交与交付

- 提交前检查索引，只暂存/提交本次路径；不要把其他会话 staged 文件一起提交。中文 conventional commits，说明实际问题和结果。
- [deliver-pr](.claude/commands/deliver-pr.md) 管检查、提交、推送和 PR；[fix-review](.claude/commands/fix-review.md) 管逐项处理反馈。
- [close-change](.claude/commands/close-change.md) 管合并、归档、关闭 Issue 与清理；执行前完整读对应文件，摘要不能替代门槛。
- PR 包含 Issue、OpenSpec/delta、实际验证和剩余风险；审核者独立只读，不直接改实现者工作区。
- 用户要求本次 `@codex review` 时可代发；先核 base/head SHA、已推送及 CI 状态。未明确要求不发，不代发 `@claude`。
- 合并、关闭 Issue、发布与后续审核分别依授权执行；使用 `Refs #编号` 关联 Issue，按验收状态手动关闭；合并不等于上线。
- **PR 一经合并（无论是否走 close-change，含只改文档的小 PR），必须当场在干净主工作区 `git pull --ff-only` 同步本地主仓 `main` 并核对 SHA**；依赖清单变了就重装依赖。主工作区有改动、已有 dev server 在跑或本地与 `origin/main` 已分叉时只报告，不强拉、不强推。来源：2026-11-13 手动合并后漏同步，被用户指出。

## 生产操作边界

- 动生产前读 [当前部署交接](docs/ops/v2-ecs-handoff.md) 和 operations 规则，重新验证必要的当前状态。
- **IMPORTANT：** V1（域名与宿主机 8000）不修改、不停止；V2 只由 `Deploy Main to ECS` 工作流发布，不用 SSH 绕开。
- **IMPORTANT：** 合并、评审通过都不是发布授权；仅当用户明确要求本次发布时才可代触发，触发前核对项见 operations 规则。
- 发布失败只报告日志和现象，不自行重试或 SSH 修补。
- 数据库没有自动备份：不默认存在恢复点，不擅自加回定时备份/逾期告警，不开放机器人恢复指令。

## 维护这些指令

- 根文件目标少于 200 行，只留广泛适用且会改变工作方式的规则；功能、接口和状态不进入根文件。
- 局部规则只写必要的禁止/必须及短理由；业务行为维护在现有 spec，设计理由维护在相关 design，操作步骤复用 command/skill/runbook。
- 一般经验复发后再晋升，先查共享记录；高风险或用户明确长期决定首次即可加入，标明来源。纠错不等待复发。
- 维护时检查有效性、自动覆盖范围/跳过条件及重复来源；“已有测试”或“长期没事故”不能单独作为删除依据。
- 变更保留可追溯去向，并同步核对 AGENTS 的专用审核口径；不要用压行数代替职责划分或覆盖验证。
