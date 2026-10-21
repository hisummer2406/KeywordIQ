# 任务与证据

执行者：Codex；2026-10-22；change/separate-agent-instructions；基准fe2870a。用户已批准本次职责重构。

- [x] 1.1 核对官方来源、当前规范与并行工作，建立独立worktree（需求：specs/agent-instruction-scope/spec.md → 根指令只承担通用工作指导；设计：D1）
  证据：design.md来源与基准，未修改主工作区。
- [x] 1.2 分离根指令、局部规则和参考导航，保留审核投影（需求：specs/agent-instruction-scope/spec.md → 局部约束按范围读取且保持单一来源；设计：D2）
  证据：CLAUDE.md、.claude/rules/、AGENTS.md、openspec/guides/、.gitignore与context。
- [x] 1.3 将既有功能说明与代码证据对齐到原规格，记录逐段去向（需求：specs/agent-instruction-scope/spec.md → 规格设计操作与历史不复制为第二套指令手册；设计：D3）
  证据：本次site-scoped-query/analysis-screen-layout delta与主规格；audit.md。
- [x] 1.4 核对规则加载格式、glob、引用、迁移、篇幅、OpenSpec与conventions（需求：specs/agent-instruction-scope/spec.md → 局部约束按范围读取且保持单一来源、规格设计操作与历史不复制为第二套指令手册；设计：D4）
  证据：audit.md：87行入口、9个YAML/代表路径、320段去向、26个Markdown链接、ignore/审核投影一致、OpenSpec37项、conventions15项、diff检查通过；不宣称实际Agent加载/业务验收。

- [x] 2.1 更新local-db目标/占用/skip判据与verify-import当前断言（需求：specs/reusable-verification-skills/spec.md → 本地验证须核实目标及跳过原因；设计：D5）
  证据：两个既有SKILL.md；仅文档编写，未运行数据库操作。
- [x] 2.2 新增分析契约验证与按需检查路由（需求：specs/reusable-verification-skills/spec.md → 分析验证复用现有测试并按影响取并集；设计：D5）
  证据：verify-analysis-contract/SKILL.md及references/check-routing.md。
- [x] 2.3 新增只读诊断并接入最小入口（需求：specs/reusable-verification-skills/spec.md → 诊断只收集证据并明确副作用边界；设计：D5）
  证据：diagnose-v2/SKILL.md、CLAUDE.md与workflow导航。
- [x] 2.4 验证4个Skill包、引用、真实入口和典型流程边界（需求：specs/reusable-verification-skills/spec.md → 三项Requirement；设计：D5）
  证据：audit.md第一批补充：4个包格式、5个文档链接、25个路径、run_import签名、37项OpenSpec及6项人工静态流程核对通过；未执行DB或生产验证。
