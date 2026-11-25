# 任务

- [x] 开始前核对主仓分支、CI、开放 PR、活动 worktree 与 ECS/V1 运行信息（D1、D2）。
- [x] 将 CI、发布门禁和 ECS 检出迁到 `main`，覆盖首轮全量、旧证据拒绝和快进路径（`v2-ecs-cd`）。
- [x] 更新仓库规则、协作指令和发布指引，编写冻结及回退步骤（D1–D4，`ops-release-control`）。
- [x] 核对并迁移旧默认分支的 Claude 工作流，自动评审面向 main 的非草稿 PR（D1）。
- [x] 本地验证通过（40 项 Node 测试、4 个工作流 YAML、部署 Bash 语法、52 项 OpenSpec strict 与 diff 检查）。
- [x] 创建 Draft PR，独立评审与切换后的 GitHub CI 按 D1 执行。
- [x] 切换窗口前更新证据，处理 #214 的合并顺序，核对导入与发布互斥并确认冻结（D1、D3）。
- [x] 按顺序改名、设默认分支、复核开放 PR 和工作流，同步本地引用（D1、D2）。
- [x] PR 在新 main 上通过 CI 和审核后合并，核对首个 main push 的三项检查与发布就绪门禁（`v2-ecs-cd`）。
- [x] 按独立发布授权核对 V2 版本和健康、机器人状态以及 V1 服务（D3）。

2026-11-25 Codex 开始实现。主仓旧 main 为 `bf67fb0`，v2 为 `fe69f5b`，
[v2 push CI 45580421182](https://github.com/hisummer2406/KeywordIQ/actions/runs/45580421182) 通过。
ECS HEAD 为 `13f58b9`，最近成功发布记录亦为该 SHA；容器对比 154 个存在的源码/配置一致，
36 个路径不在镜像内，镜像没有 revision 标签。ECS 仅跟踪 v2，origin 与机器人配置均指主仓。
V1 服务使用另一远端的 `master` / `origin/master`。可读的服务单元、系统 Cron、
V1 部署脚本和 `www` Crontab 未见拉取旧主仓 main 的配置；root Crontab 未读取。
实际改名、合并和首发仍待执行。

2026-11-26 Claude 接手：仓库已公开并改写历史，上方 SHA 均已失效（新旧对照见主仓
`.git/filter-repo-commit-map-2026-11-26`，旧 ECS `13f58b9` 对应 `87a1a97`）。#214 已合入。
本分支已变基到 v2 `c0cc4a4`，冲突按托管 runner、Action 钉 SHA、`production`
Environment 合并；Dependabot 目标改为 `main`。40 项 Node 测试、工作流 YAML 与
OpenSpec strict 52 项通过。

2026-11-26 切换与首发完成。先合入 #216/#217/#218，再把旧 `main`（`5f67458`）改名为
`v1`，`v2`（`ca89b73`）改名为 `main` 并设为默认分支。改名期间临时停用 Ruleset，之后改为
保护 `main`/`v1` 再启用；`production` 部署分支改为 `main`；旧 `main` 上 6 个 Dependabot
PR 随 base 变为 `v1` 后关闭。#215 在新 `main` 上 PR CI 与 Claude 评审通过后以合并提交
合入（`75417fd`），首个 main push
[CI 45675789043](https://github.com/hisummer2406/KeywordIQ/actions/runs/45675789043)
三项全量与「发布就绪」均通过。经用户授权运行
[Deploy Main to ECS 45676521426](https://github.com/hisummer2406/KeywordIQ/actions/runs/45676521426)
（GitHub 托管 runner + `production` 环境）成功：ECS 由本地 `v2` `87a1a97` 快进到
`main` `75417fd`，工作树干净、原 `v2` 引用保留；迁移 `b7d2e9a4c1f6` 已执行；V2
`/health` 与公网 HTTPS 健康 200，ops-bot 服务 active，V1（8000）照常响应。
飞书发布仍按 D3 冻结，恢复另行交付。

