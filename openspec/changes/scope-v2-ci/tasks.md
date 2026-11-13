# 任务

- [x] v2 push 按已验证基线累计比较并保留安全全量回退（design 基线决策）
- [x] 新增范围门禁并同步手动部署与机器人校验（v2-ecs-cd / ops-release-control delta）
- [x] 同步 AGENTS、局部规则与运维文档
- [x] 运行 Node 策略、后端检查、OpenSpec 与 diff 检查并记录实际结果
- [ ] 主仓与备用仓分别交付 PR，核实各自 CI 状态

2026-11-13 Codex：Node 策略 35 项通过；机器人定向 15 项通过；ruff、YAML 解析、diff 检查、OpenSpec strict 全部通过。真实数据库全量后端回归 1798 passed / 1 skipped（仅真实 OSS 未配置凭证）；另有机器人定向最新 15 项通过。未触发生产发布。
