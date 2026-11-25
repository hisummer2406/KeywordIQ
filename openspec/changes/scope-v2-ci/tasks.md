# 任务

- [x] v2 push 按已验证基线累计比较并保留安全全量回退（design 基线决策）
- [x] 新增范围门禁并同步手动部署与机器人校验（v2-ecs-cd / ops-release-control delta）
- [x] 同步 AGENTS、局部规则与运维文档
- [x] 运行 Node 策略、后端检查、OpenSpec 与 diff 检查并记录实际结果
- [x] 主仓范围 CI 已交付，v2 push 运行 [45580421182](https://github.com/hisummer2406/KeywordIQ/actions/runs/45580421182) 通过。按 #208 决定，备用仓退出本次交付。

手动发布规格的后续版本由 #208 的 `migrate-v2-to-main` 变更承接，避免后续归档恢复旧分支契约。

2026-11-13 Codex：Node 策略 35 项通过；机器人定向 15 项通过；ruff、YAML 解析、diff 检查、OpenSpec strict 全部通过。真实数据库全量后端回归 1798 passed / 1 skipped（仅真实 OSS 未配置凭证）；另有机器人定向最新 15 项通过。未触发生产发布。
