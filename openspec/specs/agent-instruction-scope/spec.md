# agent-instruction-scope Specification

## Purpose
定义智能体指令的职责划分：根 CLAUDE.md 只承担通用工作规则，局部约束放按 paths 加载的 .claude/rules，业务行为归 OpenSpec，操作步骤归 command/skill/runbook，避免形成第二套事实来源。

## Requirements
### Requirement: 根指令只承担通用工作指导

仓库 SHALL 将 CLAUDE.md 控制在200行以内，只保留通用工作规则、命令、验证、安全/授权边界和读取入口。
根文件 MUST NOT 维护功能模块介绍、完整API、重复配置、已开发清单或历史交付状态。

#### Scenario: 功能说明退出根入口

- **WHEN** 某段只说明一个已存在功能的行为或交付状态
- **THEN** 行为指向对应spec，设计指向design，状态查Git/Issue，不把原段继续压缩塞入根文件

### Requirement: 局部约束按范围读取且保持单一来源

局部工作约束 SHALL 使用 `.claude/rules/` 的合法paths，并在根文件提供跨Agent手动读取入口。
规则 MUST NOT 无条件全部加载，也不得通过@导入规避按需读取；不支持原生加载的Agent SHALL 手动读取影响范围的并集。

#### Scenario: 修改前端令牌清理

- **WHEN** 修改前端的失败后令牌清理
- **THEN** 同时读取frontend与auth规则，不能只因文件是前端而漏读会话约束

### Requirement: 规格设计操作与历史不复制为第二套指令手册

导航 SHALL 只列现有spec/design/skill/command/runbook入口；保留关键局部红线与Codex审核投影。
与原文相比的每段处置 SHALL 可追溯，文档静态验证不能冒充运行环境加载、生产或业务验收。

#### Scenario: 删除重复指南正文

- **WHEN** 原指南正文已由既有规格、设计或操作文件承载
- **THEN** 导航保留对应入口，审计记录原位置与当前去向，不把正文复制到另一份规则手册
