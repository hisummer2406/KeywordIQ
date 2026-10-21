# 分离 Agent 工作指令与产品文档

## Why

Refs #142，接续已合并 #144。433 行根文件及 7 份指南仍混有功能说明、技术解释与历史状态，
单纯减少行数没有分清文档职责。用户明确要求按 Anthropic 官方建议和通行的指令分层方式重构。

## What Changes

- 根 CLAUDE.md 只保留广泛适用的工作指令，目标少于 200 行；局部约束采用 `.claude/rules/` paths。
- 原指南改成规格、设计和操作入口，功能不再复制第三份；Codex 按根表手动读同一规则源。
- AGENTS 保留环境适配及专用 Code Review Rules；规则文件显式纳入 Git，OpenSpec context 只引用入口。
- 把原说明中已存在但未写入规格的关键词匹配、品牌排除、隐藏类目与日视图变化率对齐到现有规格。

## 后续明确批准的第一批能力沉淀

用户在职责重构后批准：修正local-db/verify-import，新增verify-analysis-contract/diagnose-v2。
复用现有脚本和测试，不新建发布、认证、回填或采集Skill；本次编写/静态验证不执行生产或共享测试库操作。

## Impact

只有 Markdown、规则 frontmatter、Git ignore 和 OpenSpec 上下文；不改后端/前端/插件、数据库或部署。
日/周报均不改变业务行为；历史说明从当前指令退出，以 fe2870a 的 Git 记录及原 Issue/design 溯源。
不把路径 YAML 静态验证称为 Claude Code/Codex 实际自动加载验证，不把文档整理称为业务验收。
