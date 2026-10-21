# 规格、设计与操作导航

本目录只维护入口，既不定义第二套功能规格，也不作为需要全量加载的 Agent 手册。
根目录 [CLAUDE.md](../../CLAUDE.md) 提供通用工作规则，`.claude/rules/` 提供按范围加载的局部约束。
改什么就读相关规则和规格，跨域取并集；代码符号与参数从源文件读取，状态从 Issue/PR/交接核实。

| 参考领域 | 入口 |
|---|---|
| 数据、查询、词库、首次入榜 | [analysis](analysis.md) |
| 导航、状态与展示 | [frontend](frontend.md) |
| 竞对与插件 | [competitor](competitor.md) |
| 导入与批次 | [ingestion](ingestion.md) |
| 认证与权限 | [auth](auth.md) |
| 生产与运维 | [operations](operations.md) |
| 开发操作 | [workflow](workflow.md) |

旧指南正文的去重依据和去向见 [职责重构审计](../changes/separate-agent-instructions/audit.md)。
