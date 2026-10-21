# 任务与证据

执行：Codex，2026-10-21；本地分支 change/compact-agent-guidance，基于 ef69c60，用户已批准执行。

- [x] 1.1 检查并行变更、建立独立 worktree 并保留原未提交文档（需求：specs/agent-guidance/spec.md → 维护来源与验证边界可追踪；设计：D3）
  证据：原主工作区不变，source 基准与哈希见 audit.md；未读写生产数据。
- [x] 1.2 压缩主文件并建立按域指南与读取入口（需求：specs/agent-guidance/spec.md → 常驻入口具有篇幅与完整性边界、按改动范围强制加载细则；设计：D1/D2）
  证据：CLAUDE.md、openspec/guides/README.md 及 7 个领域指南。
- [x] 1.3 核对原段落去向，消除已确认的旧参数和失效描述（需求：specs/agent-guidance/spec.md → 维护来源与验证边界可追踪；设计：D2/D3）
  证据：audit.md；AGENTS 入口、openspec/config.yaml；审核规则段保持原样。
- [x] 1.4 验证行数/字符量、迁移覆盖、链接、ignore、严格校验及文档相关 conventions（需求：specs/agent-guidance/spec.md → 常驻入口具有篇幅与完整性边界、维护来源与验证边界可追踪；设计：D4）
  证据：audit.md：433 行、字符减少74.5%、284段/1409非空行去向覆盖、本地链接/ignore、AGENTS审核段一致、OpenSpec 36项、conventions 15项、diff --check通过；不宣称生产验证。
