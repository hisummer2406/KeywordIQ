# 领域指南读取入口

根目录 [CLAUDE.md](../../CLAUDE.md) 是所有 Agent 的首读入口；以下指南是其细则附录。
跨域红线与流程先按主文件执行，动手前完整读命中范围的指南，涉及多个领域时取并集。
所有代码路径、反例中的代码符号和命令工作目录相对仓库根目录，Markdown 链接相对所在文件。
历史测量、上线/真机记录只证明记录时的状态；本次任务须在实际声称的层面重新验证。

| 指南 | 触发范围 | 主要内容 |
|---|---|---|
| [analysis](analysis.md) | 模型、分区、保留、查询/导出、增长榜、词库、趋势 | 作用域、写入纪律、索引、缺失态与口径 |
| [frontend](frontend.md) | 导航、分析页、首次入榜、保存条件、缓存 | 三 hook、筛选与展示、首次证据映射、权限规范化 |
| [competitor](competitor.md) | 关注、ASIN 上报、Shopify、上新流、插件 | 共享/私有、基线、幂等、采集完整性与分发 |
| [ingestion](ingestion.md) | 导入/重导/批次、OSS | STS、UPLOADING/QUEUED、COPY、前置校验 |
| [auth](auth.md) | RBAC、JWT/会话、登录/退出、钉钉 | 并发会话、身份绑定、哑哈希、前端跳转 |
| [operations](operations.md) | ECS、OSS/飞书、容量、发布 | V1 隔离、无自动备份、池预算、未完成实测 |
| [workflow](workflow.md) | 完整流程、提交、PR、审核/合并/归档 | 认领/worktree、授权、开发门禁与历史依据 |

业务需求的唯一规格仍为 `openspec/specs/` 和批准变更的 spec delta；不要在指南复制另一套需求。
维护时将完整细则放对应指南，主文件保留高风险判据、简短理由与触发入口；参数默认值维护在
`.env.example`/`config.py`，操作步骤维护在已有 skill/command/runbook。

本次分层的逐段去向与验证边界见 [迁移审计](../changes/compact-agent-guidance/audit.md)。
