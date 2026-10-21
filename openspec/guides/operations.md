# 生产与运维参考入口

本文件仅提供链接；功能行为查规格，取舍查对应 design。按当前任务选择相关项，不必全读。
实现约束：[operations](../../.claude/rules/operations.md)。

## 规格

- [containerized-deployment](../specs/containerized-deployment/spec.md)
- [v2-ecs-cd](../specs/v2-ecs-cd/spec.md)
- [ops-bot-platform](../specs/ops-bot-platform/spec.md)
- [ops-release-control](../specs/ops-release-control/spec.md)
- [ops-backup](../specs/ops-backup/spec.md)
- [ops-monitoring-alerting](../specs/ops-monitoring-alerting/spec.md)
- [v2-feishu-ops-service](../specs/v2-feishu-ops-service/spec.md)

## 设计依据

- [2026-09-15-dockerize-selfhosted-infra](../changes/archive/2026-09-15-dockerize-selfhosted-infra/design.md)
- [2026-09-17-add-feishu-ops-bot](../changes/archive/2026-09-17-add-feishu-ops-bot/design.md)
- [2026-09-20-add-v2-ecs-cd](../changes/archive/2026-09-20-add-v2-ecs-cd/design.md)

归档记录是当时的决策证据；若与现规格/批准的后续决策冲突，不将旧参数或交付状态当作当前事实。

## 操作与状态

- [部署交接](../../docs/ops/v2-ecs-handoff.md)
- [观测排障](../../etc/observability-runbook.md)
- [运维机器人手册](../../etc/keywordiq-ops-bot-runbook.md)
- [发布引导](../../.github/v2-ecs-cd.md)
