# 设计

## D1 官方依据与项目取舍

核查日期：2026-10-22。以下是作者建议与开放格式，不是统一强制的行业合规认证。

- [Anthropic memory](https://code.claude.com/docs/en/memory#write-effective-instructions)：根指令目标少于200行，说明保持简洁；局部规则可按 paths 加载，@导入不会降低启动上下文成本。
- [Anthropic best practices](https://code.claude.com/docs/en/best-practices#write-an-effective-claudemd)：保留改变工作方式的命令、约定与不显然陷阱；详细API、可从代码推导的信息、频繁变化的状态和长教程放其他文档。
- [AGENTS.md](https://agents.md/)：给Agent提供可预测的工作入口，并支持按子项目细化；不要求把产品说明维护在入口文件。

继续使用 CLAUDE.md 作为公共工作入口，避免为了改命名迁走 Codex 专用审核投影。
AGENTS 前部仅为环境适配；其 Code Review Rules 正文保持原样，维护说明改指向实际规则/spec。

## D2 职责与加载

根入口只含项目一句话、命令/验证、协作/授权、通用约束和导航。9份规则用合法 paths，只写局部工作约束。
Claude Code 可用原生路径加载；其他Agent依据根表手动读，跨域按影响范围取并集，不假设工具共享加载能力。
规则不使用 @ 导入，所有规则显式有 paths；不把原指南原封不动搬进 rules。
原7份指南压缩成相关 spec/design/runbook 索引，不新增架构说明副本或重新定义已存在的功能。
复杂操作复用 start-change/local-db/verify-import/deliver-pr/fix-review/close-change，不再复制流程正文。

## D3 功能规格的来源核对

根文件/指南中的业务行为优先归并现有规格；设计理由查对应归档 design，旧状态只保留 Git/Issue 证据。
发现 site-scoped-query 缺 ALL_TERMS/PHRASE、品牌排除和隐藏类目说明，analysis-screen-layout 仍称日/周变化率都对上一期。
根据 keyword_match.py、search_schemas.py、analysis_crud.py、filters.ts、columns.tsx 对齐文档和本次 delta，不改应用代码。
这不是新功能批准，也不宣称旧功能全部验收；代码行为、历史状态和用户意图冲突时仍须报告证据。
主规格与本次 delta 同步给审阅者核对；日后 archive 应按已同步状态处理，禁止重复追加同名 Requirement。

## D4 追踪与验证

逐段审计以 fe2870a 的根文件和8个导航前身为基准，记录每段当前工作规则、规格/设计/操作入口或历史去向。
不复制整份旧文档作为新的手册；历史正文可从固定Git提交复原。
验证根/局部规则篇幅、YAML及glob代表文件命中、无无条件规则/全量导入、链接、Git忽略、审核投影一致性、OpenSpec和conventions。
不新增验证工具入库，不运行业务全量或访问生产；实际工具加载和跨Agent使用尚需对应运行环境观察。

## D5 第一批复用能力

现有两个Skill更新过时断言/目标与占用检查；两个新Skill分别封装按影响选检查与只读证据诊断。
分析检查路由放一个按需reference，不复制测试实现；不新增可执行脚本，避免引入另一套业务口径。
默认发现保持不变；调用测试Skill不等于获得生产写授权，读取诊断不包含发送消息或探针写入。
修正generate_synthetic_data的用途说明：它是直接seed数据库的脚本，不是CSV生成器。
验证4个Skill的包结构、链接和测试/脚本入口，以及典型请求的流程/边界走查；不称作真实工具执行或DB/生产回归。
