# agent-guidance Specification

## Purpose
TBD - created by archiving change compact-agent-guidance. Update Purpose after archive.
## Requirements
### Requirement: 常驻入口具有篇幅与完整性边界

系统维护者 SHALL 将 CLAUDE.md 控制在 800 行以内，并保留跨域正确性、权限、生产与协作红线及必要理由。
MUST NOT 通过拼接超长行或仅删除空行达标，也不得将迁出等同于取消约束。

#### Scenario: 压缩原有约定

- **WHEN** 原约定从主文件迁出或合并
- **THEN** 审计记录指出原段落、处置、目标与证据，主文件仍含关键边界

### Requirement: 按改动范围强制加载细则

Agent SHALL 先读主文件，再按必读表加载所有命中范围的版本化领域指南；跨域任务取并集。
领域指南 SHALL 作为主文件的细则附录；业务要求仍由既有 specs 与批准的 delta 维护。

#### Scenario: 跨领域改动

- **WHEN** 修改导入证据及首次入榜显示
- **THEN** 读取导入、分析、前端指南，而不是只依赖主文件摘要

### Requirement: 维护来源与验证边界可追踪

文档精简 SHALL 保留本次已有授权、未提交改动中的有效约束、现有 Codex 审核规则及共享证据入口。
配置上下文 MUST NOT 继续宣称已失效的 RDS/连接池参数，也不得将文档迁移报告为生产或业务验收。

#### Scenario: 迁移历史状态记录

- **WHEN** 领域指南包含旧环境或历史测量
- **THEN** 标明其历史证据性质，并由当前配置/交接记录及实际验证决定当前状态
