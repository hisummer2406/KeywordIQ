# 任务

- [x] 开始前核对主仓分支、CI、开放 PR、活动 worktree 与 ECS/V1 运行信息（D1、D2）。
- [x] 将 CI、发布门禁和 ECS 检出迁到 `main`，覆盖首轮全量、旧证据拒绝和快进路径（`v2-ecs-cd`）。
- [x] 更新仓库规则、协作指令和发布指引，编写冻结及回退步骤（D1–D4，`ops-release-control`）。
- [x] 核对并迁移旧默认分支的 Claude 工作流，自动评审面向 main 的非草稿 PR（D1）。
- [x] 本地验证通过（40 项 Node 测试、4 个工作流 YAML、部署 Bash 语法、52 项 OpenSpec strict 与 diff 检查）。
- [ ] 创建 Draft PR，独立评审与切换后的 GitHub CI 按 D1 执行。
- [ ] 切换窗口前更新证据，处理 #214 的合并顺序，核对导入与发布互斥并确认冻结（D1、D3）。
- [ ] 按顺序改名、设默认分支、复核开放 PR 和工作流，同步本地引用（D1、D2）。
- [ ] PR 在新 main 上通过 CI 和审核后合并，核对首个 main push 的三项检查与发布就绪门禁（`v2-ecs-cd`）。
- [ ] 按独立发布授权核对 V2 版本和健康、机器人状态以及 V1 服务（D3）。

2026-11-25 Codex 开始实现。主仓旧 main 为 `bf67fb0`，v2 为 `fe69f5b`，
[v2 push CI 45580421182](https://github.com/hisummer2406/KeywordIQ/actions/runs/45580421182) 通过。
ECS HEAD 为 `13f58b9`，最近成功发布记录亦为该 SHA；容器对比 154 个存在的源码/配置一致，
36 个路径不在镜像内，镜像没有 revision 标签。ECS 仅跟踪 v2，origin 与机器人配置均指主仓。
V1 服务使用另一远端的 `master` / `origin/master`。可读的服务单元、系统 Cron、
V1 部署脚本和 `www` Crontab 未见拉取旧主仓 main 的配置；root Crontab 未读取。
实际改名、合并和首发仍待执行。
