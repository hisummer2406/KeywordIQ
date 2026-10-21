# 开发操作入口

本文件只列操作入口；共同授权与验证规则见根目录 [CLAUDE.md](../../CLAUDE.md)，规格维护见 [workflow 规则](../../.claude/rules/workflow.md)。

| 当前任务 | 阅读入口 |
|---|---|
| 完整变更认领/隔离 | [start-change](../../.claude/skills/start-change/SKILL.md) |
| 真实数据库回归 | [local-db](../../.claude/skills/local-db/SKILL.md) |
| 真实体量导入验证 | [verify-import](../../.claude/skills/verify-import/SKILL.md) |
| 列表/数量/分页/导出契约验证 | [verify-analysis-contract](../../.claude/skills/verify-analysis-contract/SKILL.md) |
| V2只读证据诊断 | [diagnose-v2](../../.claude/skills/diagnose-v2/SKILL.md) |
| 提交/推送/PR | [deliver-pr](../../.claude/commands/deliver-pr.md) |
| 处理评审反馈 | [fix-review](../../.claude/commands/fix-review.md) |
| 合并/验收转移/归档/清理 | [close-change](../../.claude/commands/close-change.md) |

不要把操作流程复制到另一份指南；执行前完整读对应文件，按当前用户授权及门槛执行。
