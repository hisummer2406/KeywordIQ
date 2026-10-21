# 指令职责重构审计

## 基准与边界

- 固定基准：`fe2870a`（已合并#144）。原CLAUDE.md为433行，原guides共1454行。
- 所有原文可用 `git show fe2870a:<原路径>` 复原，不制造另一份需要维护的完整手册。
- 用户已明确要求依官方建议执行职责重构；本次分离/去重不改变应用业务行为，不清理其他会话资源。
- 下表逐段登记职责去向；目标中的导航再指向现有具体spec/design/操作文件，不代表所有段落都需保留为指令。
- 已实现清单、历史事故数字与部署状态退出当前指令，历史证据留在固定Git、Issue/PR和归档design；当前状态依实际验证。
- 本次自检是文档和范围核对，不能证明所有原功能已验收或Agent原生加载已运行。
- 2026-10-22 更正：原 CLAUDE.md 77 行条目的「当前去向」中规则文件列错位（例如分层条目指向 ingestion、插件条目指向 auth），
  已按原文所属章节重新对应；guides 条目按「指南→同名规则」对应，复核无误。逐行去向仍为章节级对应，不代表每句话都逐字保留。

## 原文逐段去向

| 原路径:行 | 原段首句 | 处置 | 当前去向 |
|---|---|---|---|
| CLAUDE.md:1–1 | # CLAUDE.md | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:3–7 | 本文件面向所有在仓库工作的智能体；文件名沿用历史命名。先读根目录 AGENTS.md，再读本文件。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:9–9 | ## 开工必读：按改动范围加载细则 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:11–13 | 所有任务先读本文件；动手前再读下表命中的指南。涉及多个领域时取并集，不得只读摘要就实现。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:15–23 | / 改动范围或任务 / 必须先读 / | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:25–26 | 主文件保留关键判据，指南保存完整细则、反例与测试入口。业务需求及其场景仍以 openspec/specs/ | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:28–28 | ## 项目与命令 | 按职责重组标题 | CLAUDE.md 与对应导航 |
| CLAUDE.md:30–32 | KeywordIQ 是 Amazon 搜索词分析平台，FastAPI + PostgreSQL 15 + AntD Pro/Umi | 规则保留/行为归规格 | CLAUDE.md 通用部分；CLAUDE.md 开头；openspec/guides/ 对应参考入口 |
| CLAUDE.md:34–44 | bash | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:46–47 | 生产 .env 中数据库主机为容器网络 db，宿主机不能直接使用该连接；不要裸跑宿主机迁移。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/operations.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:49–49 | ## 分层与通用编码约束 | 按职责重组标题 | CLAUDE.md 与.claude/rules/backend.md 与导航 |
| CLAUDE.md:51–63 | - app/table/* 按 model、crud、service、api、schemas 分层；调用方向 api → serv | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:65–65 | ## V2 数据模型：事实只追加，站点只有一个来源 | 按职责重组标题 | CLAUDE.md 与.claude/rules/analysis.md 与导航 |
| CLAUDE.md:67–67 | 表、字段、索引和迁移细则见分析指南。V1 csv_processor.py、关键词 UPSERT 与 JSONB 趋势已退役。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:69–79 | / 表/模块 / 约束 / | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:81–89 | 1. **四类事实表只追加，不原地 UPDATE。** 排名及派生值在导入时算定；查询只读。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:91–91 | ### 分区、迁移与保留清理 | 按职责重组标题 | CLAUDE.md 与.claude/rules/analysis.md；.claude/rules/backend.md 与导航 |
| CLAUDE.md:93–102 | - 业务模型新增时登记 migration_scope.V2_TABLES；ops 模型登记 OPS_TABLES。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:104–104 | ## 分析查询、导出与排名趋势 | 按职责重组标题 | CLAUDE.md 与.claude/rules/analysis.md 与导航 |
| CLAUDE.md:106–106 | 修改该域前读分析指南；首次入榜、筛选与缓存展示还需读前端指南。接口作用域为单站点、单视图、锚定期。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:108–113 | / 维度 / DAY / WEEK / | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/analysis.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:115–136 | - 行集合为在榜 ∪ 掉榜，include=ALL/LISTED/DROPPED；掉榜恒排在在榜之后，不随升降序翻转。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:138–138 | ### 持续增长市场与我的词库 | 按职责重组标题 | CLAUDE.md 与.claude/rules/analysis.md 与导航 |
| CLAUDE.md:140–149 | - 增长榜仅日报：窗口每期在榜、累计名次净增严格超过阈值、6 个日环比均达到阈值。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:151–151 | ### 首次入榜（#137 V1 / #141） | 按职责重组标题 | CLAUDE.md 与.claude/rules/analysis.md 与导航 |
| CLAUDE.md:153–162 | - 永久证据存 dim_keyword.first_seen_date/first_seen_period_type/first_ | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/analysis.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:164–164 | ## 导入：先直传、后流式 COPY、后台串行接手 | 按职责重组标题 | CLAUDE.md 与.claude/rules/ingestion.md 与导航 |
| CLAUDE.md:166–166 | 修改前读导入指南与分析指南；改变导入路径须做真实体量端到端验证。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/ingestion.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:168–171 | 1. /oss/upload-credentials 先落 UPLOADING 批次，再交受限 STS 凭证；落库失败不能签发。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/ingestion.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:173–180 | - 导入用 pg_try_advisory_lock 串行；拿不到进 QUEUED。轮询任务必须实际接手，不能只靠上次导入结束链式 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/ingestion.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:182–182 | ## 认证与错误契约 | 按职责重组标题 | CLAUDE.md 与.claude/rules/auth.md 与导航 |
| CLAUDE.md:184–184 | 修改登录、退出、令牌清理、RBAC、SSO 前读认证指南；安全边界不能只靠前端隐藏。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/auth.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:186–186 | ### 认证、会话与权限 | 按职责重组标题 | CLAUDE.md 与.claude/rules/auth.md 与导航 |
| CLAUDE.md:188–207 | - 自建 PyJWT/HS256 + bcrypt + RBAC；没有全局鉴权中间件，受保护端点逐个挂依赖。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/auth.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:209–209 | ### 所有失败响应统一 | 按职责重组标题 | CLAUDE.md 与.claude/rules/backend.md 与导航 |
| CLAUDE.md:211–213 | json | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/backend.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:215–221 | - Service 抛 APIError 子类；API 不用 try/except 盖掉域错误码，不返元组或失败的 200 Pro | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:223–223 | ## 前端与导航：生效条件、缓存和缺失态不能混淆 | 按职责重组标题 | CLAUDE.md 与.claude/rules/frontend.md；.claude/rules/auth.md 与导航 |
| CLAUDE.md:225–225 | 修改前读前端指南；本节仅保留跨屏原则和高风险判据。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/frontend.md；.claude/rules/auth.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:227–250 | - 导航按数据域命名，八项顺序为机会中心、市场分析、词库工具、竞对监控、产品档案、风险核查、定价核算、数据管理。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/frontend.md；.claude/rules/auth.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:252–252 | ## 竞对监控与插件：共享观测，私有关注 | 按职责重组标题 | CLAUDE.md 与.claude/rules/competitor.md 与导航 |
| CLAUDE.md:254–254 | 修改前读竞对指南；插件还须读 extension/README.md 及采集变更 design。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/competitor.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:256–277 | - 共享主体/观测不带账号属性；分组/关注/预览私有且逐查询限制 user_id，别人的记录返回 404。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/competitor.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:279–279 | ### 浏览器插件（extension/） | 按职责重组标题 | CLAUDE.md 与.claude/rules/extension.md；.claude/rules/competitor.md 与导航 |
| CLAUDE.md:281–286 | - manifest 与 package 版本一致，manifest 保留公钥 key 固定扩展 ID；私钥不得入库。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/extension.md；.claude/rules/competitor.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:288–288 | ## 部署与运维：只走授权入口，V1 不动 | 按职责重组标题 | CLAUDE.md 与.claude/rules/operations.md 与导航 |
| CLAUDE.md:290–290 | 开始 ECS、OSS 或 ops-bot 工作前先读交接与运维指南；环境记录不是当前在线状态，必要时重新验证。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/operations.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:292–308 | - V2 只由 Deploy V2 to ECS 手动发布。必须为 refs/heads/v2、完整 SHA 位于 origin/ | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/operations.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:310–310 | ## 流程重量：默认最轻，长期决策才留完整规格 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:312–312 | 沿用三档；用户已明确范围与方案时直接按授权推进。必要澄清一次问完，不串行多轮追问。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:314–318 | / 档位 / 产物 / 适用 / | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:320–322 | 取舍看决策以后是否需要翻阅，不按改动大小；一次性脚本、测试辅助或提交信息不展开长篇背景。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:324–324 | ### 跨 Agent 交付：Issue 是契约，PR 是审核包 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:326–327 | 默认 Codex 讨论、Issue 交接、Claude Code 实现、Codex 独立审核；用户可另行指定执行者。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:329–334 | 1. 完整流程先读 .claude/skills/start-change/SKILL.md，认领并建独立仓库外 worktree | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:336–336 | #### 代发 @codex review | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:338–341 | 仅用户当次明确要求“让 codex 评审/再发一轮复审”时可代发，不因 deliver-pr/fix-review 自行触发。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:343–343 | ### 工作分支与并行会话 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:345–358 | - 所有变更从 v2 建分支，通常 change/<id>，不是 main；PR 显式 --base v2，创建后核对。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:360–360 | ### 手动交付入口 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:362–366 | - ［/deliver-pr］(.claude/commands/deliver-pr.md)：检查、提交、推送、创建/更新 PR | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:368–368 | ## 验证与开发 harness | 按职责重组标题 | CLAUDE.md 与.claude/rules/backend.md 与导航 |
| CLAUDE.md:370–371 | Codex 等环境不受 Claude Code PreToolUse hook 保护，必须主动跑检查；详见 AGENTS.md。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:373–378 | / 改动范围 / 提交前检查 / | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/backend.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:380–388 | - 前端可用 vitest related 缩小影响，但 .umirc.ts、vitest.config.ts、setup.ts  | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/backend.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:390–390 | ## CLAUDE.md 的维护纪律（#142） | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:392–393 | 主文件目标 700～780 行、上限 800 行；同时检查实际文本量，不靠去空行或长行拼接凑数。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:395–395 | ### 经验晋升 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:397–401 | 一般经验首次记共享 Issue 或版本化 OpenSpec，记录问题/根因/范围/证据；本地 worklog 不作为唯一依据。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:403–403 | ### 审计与处置 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:405–407 | 用户发起、大版本收尾或发现冲突时审计，不默认建定时任务。记录基准提交/本地差异、范围与未核实项。 | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:409–414 | / 处置 / 判据 / | 规则保留/行为归规格 | CLAUDE.md 通用部分；.claude/rules/workflow.md；openspec/guides/ 对应参考入口 |
| CLAUDE.md:416–418 | 删改清单逐项列原条目、证据、去向、确认状态与执行结果。#142 首轮逐条处置仍需逐条确认； | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:420–420 | ### 完整流程的双向覆盖 | 按职责重组标题 | CLAUDE.md 与.claude/rules/workflow.md 与导航 |
| CLAUDE.md:422–423 | 只约束新完整流程变更，轻量两档与已归档变更不补矩阵；沿用 tasks 内联标注，不新增独立矩阵文件。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:425–428 | text | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| CLAUDE.md:430–433 | PR 审核、合并前从需求查 task/实际改动/验证，反向从 diff 查批准范围；特别查非目标和砍掉功能是否被实现。 | 通用规则精简/流程去重 | CLAUDE.md；.claude/rules/workflow.md；既有skill/command；原文历史见 fe2870a |
| openspec/guides/README.md:1–1 | # 领域指南读取入口 | 导航替换 | CLAUDE.md；openspec/guides/README.md |
| openspec/guides/README.md:3–6 | 根目录 ［CLAUDE.md］(../../CLAUDE.md) 是所有 Agent 的首读入口；以下指南是其细则附录。 | 导航替换 | CLAUDE.md；openspec/guides/README.md |
| openspec/guides/README.md:8–16 | / 指南 / 触发范围 / 主要内容 / | 导航替换 | CLAUDE.md；openspec/guides/README.md |
| openspec/guides/README.md:18–20 | 业务需求的唯一规格仍为 openspec/specs/ 和批准变更的 spec delta；不要在指南复制另一套需求。 | 导航替换 | CLAUDE.md；openspec/guides/README.md |
| openspec/guides/README.md:22–22 | 本次分层的逐段去向与验证边界见 ［迁移审计］(../changes/compact-agent-guidance/audit.md | 导航替换 | CLAUDE.md；openspec/guides/README.md |
| openspec/guides/analysis.md:1–1 | # 分析、词库与数据模型 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:7–7 | ## V2 数据模型 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:9–12 | V1 已完成使命并退役，V2 是完整重建。数据模型、导入、查询、界面均已落地，V2 已在阿里云 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:14–14 | **V2 的表**（星型模型，全部在 analysis schema 下）： | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:16–25 | / 表 / 说明 / | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:27–28 | 分区表的注册表是 partitioning.PARTITIONED_TABLES（表名 → PartitionScheme(dat | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:30–30 | **三条不可违反的约束**： | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:32–43 | 1. **事实表只追加，不原地更新。** 排名、上期排名、排名变化都在写入时算定。 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:45–47 | **迁移注意**：分区表与物化视图不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:49–52 | **新增模型必须登记进 migration_scope.py 的 V2_TABLES**（alembic/env.py 的 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:54–59 | **列注释参与 autogenerate 比对**，因此模型里的注释文本要与库里一字不差；改注释就要配 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:61–63 | **V2 查询层（site-scoped-query + window-scoped-rows，已落地）**：分析查询查 V2 星 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:65–69 | / / 天视图 / 周视图 / | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:71–73 | **行集合 = 在榜 ∪ 掉榜**：在榜 = 锚定期有数据；掉榜 = 行窗口内出现过但锚定期没有（读 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:75–78 | **但带筛选的路径仍然锁死在单一事实切片里。** 筛选一律以锚定期那一行为准,必然后果是 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:80–80 | 要点： | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:82–110 | - **日期选择器走 site_data_stats**（不在事实全表上实时聚合；在榜总数因类目黑名单已改为实算，见分析查询屏一节 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:112–114 | **关键词工具 · 持续增长市场（/api/keyword/growth/*，权限点 keyword:view）**：近 7 天名 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:116–127 | - **只有日报**。周报没有「连续 7 天」，period='WEEK' 的导入不算定、不写表。 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:129–132 | 代价是空表格从两种成因变成**三种**——还没查 / 窗口缺期 / 口径下确实没命中——含义 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:134–136 | **关键词工具 · 我的词库（/api/keyword/library/*，权限点同为 keyword:view）**：用户自建的 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:138–158 | - **行集合由条目决定，事实表只补数**——与分析查询屏相反（那边行集合 = 在榜 ∪ 掉榜， | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:160–161 | 事实行的列投影由 analysis_crud.fact_metric_columns 提供，**两屏共用一份**；复制第二份 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:163–173 | **关键词工具 · 排名趋势详情（GET /api/keyword/trend，issue #86）**：单关键词的日/周排名 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:175–200 | - **五态分类，且不信任 site_data_stats 在保留下界之前的行。** retention_service | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:202–207 | **数据保留：只保留最近三个月。** 由 HOT_WINDOW_MONTHS（默认 3）定义热窗口，报告日期 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:209–215 | **分区移出必须同时清理 site_data_stats/category_stats/brand_stats 的对应残留 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:217–217 | ### Database Design | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:219–219 | **Schema**: analysis | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:221–223 | 表结构见上文「V2 数据模型」一节（星型模型：dim_keyword / dim_product / | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:225–228 | **category_stats / brand_stats**：普通表，按 (站点, 周期, 日期) 分片提供类目/品牌 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:230–230 | ### Performance Optimizations | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:232–236 | 1. **Connection Pooling**: database.py 在模块级建了**两个**引擎，各自持有独立的池， | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:238–241 |  | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:243–249 | start.sh/Dockerfile 固定 4 workers。当前取值 (2+3)×4 + (8+8)×4 = 84，为 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/analysis.md:251–274 | **THREAD_POOL_LIMIT（默认 32）与连接池无关，调池时不要跟着一起动。** anyio 的默认 | 分离约束与参考/历史 | .claude/rules/analysis.md；openspec/guides/analysis.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:1–1 | # 认证、权限与钉钉登录 | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:7–7 | ### Authentication & Authorization | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:9–24 | - **自建 JWT + RBAC**（fastapi-user-auth/fastapi-amis-admin 已随 amis  | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:26–32 | 两条历史教训写在这里，别再走回去：**（1）** 曾有 user:manage 权限点，收紧后它成了 | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:34–57 | 代价是**能管用户的唯一判据就是 is_super**：若唯一超管的该位被误改为 false，界面上 | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:59–61 | **钉钉单点登录（/api/auth/sso/*，全部免鉴权）**：员工扫码即可登录，不必在本系统单设 | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:63–95 | - **这套不是 OIDC，尽管授权参数里的 scope 叫 openid。** 钉钉换回来的是 accessToken， | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:97–102 | 前端入口在登录页（pages/Login）与过场屏 pages/SsoCallback（路由 /sso/callback， | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/auth.md:104–108 | **回调域名配置与真机扫码无法靠 code review 确认**，用 uv run python scripts/check_d | 分离约束与参考/历史 | .claude/rules/auth.md；openspec/guides/auth.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:1–1 | # 竞对监控与浏览器插件 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:7–7 | ## 竞对监控（/api/competitor/*，权限点 competitor:view） | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:9–15 | 盯别人的店：把竞对卖家攒成关注清单，供后续的上新监控使用。**P0（关注清单）、P3a | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:17–18 | **插件的两处限制与一处已澄清的误解，改动前先读 extension/README.md 与对应变更的 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:20–31 | - **令牌是从已登录网页里同步来的 24 小时 JWT，全权限**（session-sync.ts 读网页 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:33–35 | **采集是「多页本地合并、一次上报」，不是逐页上报。** 逐页上报会让第一页单独落定该卖家的基线， | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:37–39 | **分发方式是 zip + 加载已解压，不上 Chrome Web Store。** pnpm run pack（extensio | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:41–49 | - **版本号有两处，必须一致**：manifest.json 与 package.json。打包脚本与 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:51–51 | **两层数据模型，分层的判据是「这条数据属于谁」**： | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:53–60 | / 表 / 层 / 说明 / | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:62–62 | 六张都是普通表，不分区、不参与保留清理、不被导入路径写入。 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:64–65 | 分共享层的理由是抓取代价：后续上新监控要按卖家抓商品，**一个卖家被十个用户关注也只该 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:67–67 | **与词库（keyword_group）刻意不同的两点**，抄错了不会报错： | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:69–74 | - **分组不绑站点。** 这条后来还多兑现了一次：独立站监控落地时，同一个分组直接就能装 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:76–81 | **解析器 seller_ref_parser.py 是纯函数，书签导入与手工粘贴共用一份。** 用户从浏览器 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:83–86 | **站点识别的优先级是 marketplaceID > 域名 TLD > 调用方给的 fallback_site。** | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:88–88 | **导入是预览 → 确认两步，配额在预览阶段就判定。** 两条约束： | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:90–94 | - **超配额整批拒绝、不截断**，且判定发生在预览而非确认——让用户看完 2657 条的预览再告诉 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:96–99 | 预览暂存进表而不是 Redis，是为了不给这一个功能引入一个新的基础设施依赖；过期行清理 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:101–103 | **权限点 competitor:view 是独立的，不复用 keyword:view。** 与「外观专利」那种占位屏 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:105–105 | ### 竞对卖家 ASIN 上报（/api/competitor/asin-reports，issue #56 P3a 服务端侧） | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:107–110 | **推模型，不是拉模型。** 采集端（浏览器插件，不在本变更范围）主动 POST 它在某卖家页面 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:112–112 | **由此推出三条边界，缺一条都会在某个时候制造出一批安静的假数据：** | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:114–124 | 1. **不做下架判定。** "这次没见到"与"已下架"在数据上无法区分。与独立站「下架判定只在 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:126–134 | **基线 = 第一次上报，一旦落定不再改动。** 某卖家的首次上报把 baseline_at 落成本次 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:136–140 | **时间的权威只有服务端。** baseline_at 与 first_seen_at 一律取服务端事务时间；上报体 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:142–142 | **幂等靠两把锁，缺一把只挡住一半：** | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:144–155 | - request_id（客户端生成，**必填**，按 (user_id, request_id) 唯一）——命中即回放原 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:157–160 | **每行在售关系记录首次引入它的上报**（first_report_id，ON DELETE SET NULL）。共享层 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:162–165 | **上报只接受上报者本人已关注的卖家，整个请求拒绝（不是逐条）。** 一次上报只对一个 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:167–171 | **单条 ASIN 格式非法逐条拒绝、其余照写；条数超限整批拒绝、不截断。** 判据是"这样做会不会 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:173–177 | **零条有效 ASIN（请求为空，或全部条目因格式非法被逐条拒绝）整批拒绝（422），MUST NOT | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:179–181 | **非法站点码返回 422，不是 500。** normalize_site_code 抛 UnknownSiteError， | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:183–196 | **上新流在 SQL 里合并两类来源，不在应用层归并两页**（store_crud.new_arrivals_stmt 对 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:198–201 | **「点开一行看详情」不在本变更范围内**（两类来源都没有这个交互，不是 Amazon 分支特有的 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:203–205 | **competitor_seller_asin/competitor_asin_report 不参与按月保留清理**（不登记进 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:207–211 | **配送地区仍不用于过滤或存活判定，只留档。** 实测 Amazon 店铺搜索结果按操作者账号收货 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:213–213 | ### 独立站监控（Shopify，/api/competitor/stores*） | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:215–216 | 与 Amazon 卖家**共用分组、权限点与上新流，不共用表与字段**。分的是数据与采集，合的是 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:218–220 | **为什么先做这条**：它没有未决前提。/products.json 是公开端点，服务端一个 HTTP 请求即可， | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:222–228 | / 表 / 粒度 / 写入模式 / | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:230–232 | 五张普通表，**MUST NOT 登记进 PARTITIONED_TABLES**——登记即被保留清理按 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:234–234 | **七条不可违反的约束**，每条都对应一个「错了不会报错」的失败形态： | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:236–257 | 1. **下架判定只在 catalog_complete 为真时做。** 完整 = 每页 200 + 翻页因空页自然终止 + | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:259–265 | **加入入口有两个，共用一份域名规范化**：单条表单（长期主路径）与批量粘贴（一次性初始 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:267–270 | **拉取挂在 import_queue.py 已有的轮询循环上**，按 last_fetch_at 状态列认领 | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/competitor.md:272–273 | **本变更不做取关后的 GC。** issue #56 定过「保留 7 天」，但那是按每天全量轮询的量写的； | 分离约束与参考/历史 | .claude/rules/competitor.md；openspec/guides/competitor.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:1–1 | # 导航与分析界面 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:7–7 | ## 顶栏菜单：按数据域命名，不设按排除法命名的容器 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:9–14 | 顶栏八项，名称统一四字，顺序固定：**机会中心 → 市场分析 → 词库工具 → 竞对监控 → | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:16–21 | **新增一级菜单的判据是「它读哪个数据域」，没有一项匹配就新开一个。** 这里曾有一个叫 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:23–27 | **单页顶层（/opportunity、/analysis、/competitor、/products、/pricing）不得加二 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:29–41 | **机会中心（/opportunity，issue #76）是承接"确定性 Signal → 候选机会 → 验证推进 → | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:43–47 | **风险核查（/ip，审核待办 / 外观专利 / 商标查询 / 风险案例，原名「侵权查询」）只是占位屏**：仓库里没有 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:49–51 | **四字导航（2026-09-05）**：导航简称不强制缩短业务页面标题。「市场分析」页面仍可显示 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:53–54 | 产品档案（/products）与定价核算（/pricing）本轮只提供明确标注尚未接入的入口， | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:56–62 | **占位屏仅超管可见（2026-09-20）**：产品档案、风险核查、定价核算三个一级菜单挂前端 access | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:64–65 | 把它放进顶栏是自觉取舍：产品最空的一块因此成了最显眼的一块，换来的是「这块在做」这个 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:67–68 | 旧地址 /toolbox、/toolbox/growth、/toolbox/library 保留重定向；两个占位屏的旧地址 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:70–70 | ## 分析查询屏 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:72–73 | **后台 UI 是独立的 AntD Pro SPA**（frontend/，Umi Max + Ant Design + ProC | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:75–76 | **分析查询屏（frontend/src/pages/Analysis/）分三段**：顶部作用域栏（站点/视图/日期/检索框/ | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:78–85 | **状态编排拆成三个 hook，依赖方向单向**（issue #69 阶段二）：useAnalysisSession（会话快照、 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:87–90 | - **首屏按默认作用域自动查一次**。进屏先拉站点、作用域与类目（选择器的前提），拿到结果 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:92–95 | **默认值只有一处定义**：filters.ts 的 defaultFilters()，初始草稿、首屏自动查询、 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:97–98 | **条件初始化有三级优先级**（savedMode.ts），高者胜出：**会话快照 > 用户保存的条件 > | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:100–104 | / / 存活期 / 存在哪 / 谁清它 / | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:106–108 | 保存的条件（「保存当前模式」，对标卖家精灵）存**站点 + 视图 + 筛选 + 排序**， | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:110–120 | - **它必须活过 doLogout，这不是漏清。** 会话快照是**无主的单份**，换账号会串，只能清； | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:122–124 | **首屏按保存的作用域初始化时，reloadScopeMeta 必须带 keepCategories**。它平时会清空 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:126–127 | 首屏这次查询**带筛选**，两个后果都是既有语义、不是本屏特例：总数走 count_filtered | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:129–132 | 自动查询**只发生在无会话缓存的首次进入**（if (restored) return; 那道闸之后），切走 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:134–249 | 站点一期数据都没有时不发请求，此时空表格**必须说明是没有数据**（scopeHasNoDates）， | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:251–261 | **competitorNew 必须计入前端 FILTER_KEYS**（useAnalysisQuery.ts）——它决定「本次 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:263–267 | - **结果表格可勾选行并批量「加入词库」**（唯一的入库入口，见 ［分析指南］(analysis.md) 的词库部分）。可选分组 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:269–270 | 条件转换逻辑在 filters.ts（纯函数，vitest 直接测）；展示组件 ScopeBar / CategoryPanel  | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:272–275 | **切走再切回不刷新，这是约定不是缺陷。** umi 4.6 没有 keepalive，路由一变这个组件真的被 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:277–285 | - **短路只作用于复原后的第一次请求**（一次性）。写成「参数相同就用缓存」会把同参数的 | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/frontend.md:287–290 | 滚动位置一并恢复，且**必须等结果行落到 DOM 之后**——行没渲染时页面高度不足，scrollTo | 分离约束与参考/历史 | .claude/rules/frontend.md；openspec/guides/frontend.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:1–1 | # CSV 与 OSS 导入 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:7–7 | ### Data Import Flow | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:9–9 | **V2 OSS-first 导入（现行，日报周报同一套流程）**：表格文件**先直传 OSS，再从 OSS 导入**。 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:11–22 | 1. **签发直传凭证** POST /oss/upload-credentials（upload_api.py → oss_cl | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:24–35 | **维度补齐只在属性真变时才写。** dim_product 的 UPSERT 带 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:37–39 | **object key 约定**：<OSS_KEY_PREFIX>/<SITE>/<PERIOD>/<report_date>/ | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:41–45 | **配置**：OSS_*（见 .env.example）；OSS_ENABLED=false 时走本地文件退路 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:47–50 | **bucket 的 CORS 不在本仓库配置范围内，但直传依赖它**：来源需含前端域名、方法需含 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:52–56 | **上线前必改**（开发环境放宽处，清单与理由见 .env.example 的 OSS 段）：CORS 来源从 * | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:58–59 | **导入串行**：pg_try_advisory_lock 保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级导入并发会争抢 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:61–64 | **QUEUED 必须真的会被接手**，否则它就是个死标签。接手者是 app/table/upload/import_queue. | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:66–66 | 因此两条约束： | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:68–72 | - **接手用轮询，不用「导入结束时接着跑下一个」**。链式接力在进程被杀、导入崩在半路、或前一个 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:74–78 | **UPLOADING 适用同一条纪律**（import_service.reap_stale_uploads，挂在同一个轮询任务 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:80–83 | - **在** → 字节传完了、只是没触发导入（关标签页的典型形态）：置 QUEUED 交给上面那个接手者， | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:85–90 | **导入批次有两个读取出口，职责不同、不要合并**：上传屏的「最近导入状态」看板 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:92–93 | **重导**（POST /batches/{id}/retry，权限点同为 upload:import）凭批次留存的 object | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:95–100 | - **只允许 FAILED。** 重导会先删该 (站点, 周期, 期次) 的现有切片再重写；对 COMPLETED | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:102–106 | 准入判定（不存在 404 / 非 FAILED 409 / object_key 空 422 / 前缀越权 400 / 对象已删或 | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/ingestion.md:108–109 | > V1 的 csv_processor.py（pandas 分块 + ON CONFLICT (keyword) UPSERT  | 分离约束与参考/历史 | .claude/rules/ingestion.md；openspec/guides/ingestion.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:1–1 | # 生产边界与容量 | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:7–7 | ## Production Environment | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:9–11 | **服务出问题排查看 etc/observability-runbook.md**（怎么用 request_id 关联 nginx | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:13–16 | / Category / Spec / | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:18–20 | 应用与数据库不再是两台独立机器——dockerize-selfhosted-infra 变更之前是阿里云 RDS | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:22–23 | **V2 上线拓扑（2026-09-19）**——细节与实时状态以 docs/ops/v2-ecs-handoff.md 为准，这 | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:25–39 | - **V1 与 V2 同机、完全隔离。** V1 仍是裸部署（域名 amazon.liangtong.online、后端 800 | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:41–44 | **飞书运维机器人（ops_bot/）是与业务 app 完全独立的 host 级 systemd 服务** | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:46–63 | - **它崩溃、重启不影响业务 app，反之亦然**；app/ 不 import 它，它也不依赖 app.*。 | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/operations.md:65–78 | **Capacity implications for code changes:** | 分离约束与参考/历史 | .claude/rules/operations.md；openspec/guides/operations.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:1–1 | # 开发流程与历史依据 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:3–5 | 本指南是根目录 ［CLAUDE.md］(../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:7–7 | ## 流程重量：先问，别默认最重 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:9–9 | **开工先问一句这件事要多重，默认取最轻的那档。** 三档： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:11–15 | / 档位 / 做什么 / 适用 / | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:17–18 | 判据是**这个决策以后还要不要被翻出来看**，不是改动大小。「账号按 unionId 还是 userid 匹配」 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:20–20 | ### 为什么要有这条 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:22–24 | 2026-08-19 的钉钉 SSO 变更（issue #36）事后统计：生产代码 949 行、测试 1106 行、 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:26–29 | 原因不是哪一步做错了，而是**默认把所有重流程叠满**：brainstorming 判成 architectural → | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:31–31 | ### 四条具体约束 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:33–35 | 1. **澄清问题一次问完。** 一个 AskUserQuestion 最多带 4 个问题，**不要串行问 4 轮**—— | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:37–40 | 2. **篇幅按「反事实是否显然」定，不要一律拉满。** 领域指南这种「解释关键约束不这么做会怎样」 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:42–45 | 3. **先验前提，再设计。** 有没有那个租户 / 权限 / 数据源 / 收不收费——先问或先查。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:47–50 | 4. **测试按影响面跑，全量只在提交前一次。** 前端全量约 97 秒且随用例数增长； | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:52–52 | ## 跨 Agent 交付流程：Issue 是交接契约，PR 是审核包 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:54–56 | 默认由用户在 **Codex 桌面端**讨论需求；讨论收敛后，先创建 GitHub Issue，再由 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:58–58 | 完整流程如下；小改动仍按上一节的「直接做」或「先对齐再做」分流，不为流程而流程： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:60–64 | text | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:66–66 | ### 1. Issue：跨工具的唯一交接物 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:68–71 | Issue 不得只有标题或聊天摘要；至少写明**背景与目标、可验证的验收条件、非目标、已决定的 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:73–73 | ### 2. 实现与 PR：把契约和证据一并交付 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:75–77 | Claude Code 先按 Issue 判断流程重量；需要长期留档的变更走 OpenSpec，并遵守下文的认领与 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:79–79 | #### Claude Code 代发 @codex review | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:81–84 | Codex 评审由 PR 下的 @codex review 评论触发（GitHub 上的 Codex 应用响应，评论作者是用户自己 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:86–87 | 代发前先核对：PR 存在且 base 为 v2；本地提交已推送，PR 的 headRefOid 等于要评审的提交； | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:89–91 | bash | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:93–94 | 可在评论里补一两行"请重点看"的范围，不贴聊天或日志。发完只报告评论链接，不轮询等待评审结果 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:96–96 | ### 3. 审核与修复：独立视角，但不越权改分支 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:98–101 | Codex 审核 Issue / OpenSpec / PR diff 是否一致，并检查数据正确性、权限边界、迁移与测试缺口。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:103–103 | ### 4. 合并与收尾：v2 的 Issue 要手动关闭 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:105–107 | 审核通过后才合并进 v2。GitHub 只会在合并到默认分支时处理 Closes #...，而默认分支是 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:109–109 | ## 工作分支是 v2，不是 main | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:111–112 | V1 已完成使命并退役，V2 是完整重建（见根目录 CLAUDE.md「V2 数据模型」）。**所有工作基于 v2**， | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:114–114 | 两条直接后果： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:116–118 | - **开分支从 v2 拉**（git worktree add -b "change/$ID" ... v2）。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:120–121 | 同理，Closes #<n> 在合并进 v2 时**不会触发关闭**（GitHub 只在合并进默认分支时关 issue）， | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:123–125 | **V2 不逐个发布**：各变更合进 v2 后不单独上线，整体做完再一次性发布。列剩余工作时不要 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:127–130 | **用户在对话里明确要求发布时（如「发布」「部署到服务器」），Claude Code 直接代为触发** | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:132–137 | - 目标 SHA 是 origin/v2 最新提交，本地无未推送提交； | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:139–141 | 触发用 gh workflow run "Deploy V2 to ECS" --ref v2，随后 gh run watch 到 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:143–143 | ## 并行会话：一个变更一个 worktree | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:145–146 | **这个仓库经常同时有多个会话在干活。** 默认假设不是「只有我一个人」。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:148–148 | ### 一个变更一个 worktree，路径在仓库外 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:150–155 | 每个 openspec 变更配一个分支 change/<变更id> 和一个 worktree，路径 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:157–157 | worktree 解决的是**共享索引与共享工作树**这两类碰撞： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:159–160 | - git commit 提交的是整个索引，别的会话 staged 的文件会被一并带走（真实发生过） | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:162–162 | ### worktree 解决不了的三类，要另外守 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:164–169 | 1. **重复劳动。** 两个 worktree 不阻止两人做同一件事，只会把撞车从「改到一半」 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:171–177 | **反过来也成立：跑一次全量 pytest 会清空这个库里的业务数据。** | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:179–183 | 同理，集成测试里的计数**必须带作用域**（WHERE site=... AND report_date=...）。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:185–185 | ### 提交前看索引列 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:187–189 | git status --short 的**第一列是索引**。看到不属于本次改动的条目，用 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:191–196 | **门禁会拦一半。** 索引与工作树对同一文件不一致时（暂存后又改了、或暂存后从磁盘 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:198–199 | 门禁**拦不住**的那一半：索引里那些与你无关、内容又和磁盘一致的文件（别的会话暂存 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:201–211 | > **「暂存区是空的」这件事本身漏过两次，成因不同。** 一次是查错了仓库：会话在 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:213–213 | ## 开发 harness（提交门禁） | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:215–216 | **开工先 openspec list。** 手上的活儿很可能正是某个在飞变更里没勾完的任务， | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:218–218 | ### 提交门禁 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:220–223 | git commit 之前会自动按改动范围跑验证，不通过就拒绝提交。挂在 PreToolUse 上 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:225–230 | / 改动范围 / 跑什么 / 耗时 / | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:232–235 | **这些检查并发跑**（run_checks），墙钟是最慢那条而不是各条之和：前后端都改时实测 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:237–239 | 本地 keywordiq_test 连得上时会自动带上 TEST_DATABASE_URL 解开集成测试，连不上就 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:241–243 | 每条检查有 300 秒超时，超时按失败处理。卡住的 pytest（抢咨询锁、半开连接）若没有 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:245–245 | ### 铁律检查在哪 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:247–247 | 分两处，**不重叠**： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:249–253 | - **ruff**（pyproject.toml 的 ［tool.ruff.lint］）——能靠「看一个文件」判定的： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:255–256 | 新增铁律前先问「ruff 能不能查」，能就别手写。手写的规则**必须自带正反 fixture** | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:258–258 | ### 基线棘轮 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:260–262 | 规则建立之前就存在的违规登记在 test/conventions_baseline.toml，被放行，好让门禁 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:264–266 | **基线当前是空的**——建立规则时登记的三笔债已全部清偿。空基线是常态，不是终点： | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:268–270 | 历史债务（返值式错误、上传死配置、连接池漏算）已清偿，旧参数不作为当前配置依据。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:272–272 | ### 项目 skill | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:274–281 | - **start-change** —— 开始或接手一个变更时先跑它：认领（防止与并行会话重复劳动）、 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:283–283 | ### 项目 command（手动调用） | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:285–285 | 交付、修复与合并由用户分别触发；流程文件放在 .claude/commands/，不另建同名 skill。 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:287–295 | - **［/deliver-pr］(../../.claude/commands/deliver-pr.md)** —— 按约定检 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:297–297 | ## 当前部署交接 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |
| openspec/guides/workflow.md:299–300 | V2 ECS、OSS 与飞书运维机器人的实时交接状态统一记录在 | 分离约束与参考/历史 | .claude/rules/workflow.md；openspec/guides/workflow.md 的 spec/design/操作链接；原文历史见 fe2870a |

## 关键边界核对

| 边界 | 当前权威入口 |
|---|---|
| 事实只追加、变化才写、站点、分区/迁移/保留锁 | analysis/backend规则；AGENTS专用审核投影 |
| 历史索引、on、五态、首次证据/乱序、私有权限 | analysis/auth规则；对应spec |
| 单一会话签发、并发返值、条件退出、异步令牌清理 | auth规则；single-session-login规格 |
| 采集基线、幂等、零有效上报、catalog_complete、排序身份 | competitor规则；ASIN/DTC规格 |
| 插件key/版本/host权限/多页一次上报 | extension规则与插件规格/README |
| V1隔离、工作流三门、无自动备份、原发布授权 | 根文件+operations规则；交接与发布规格 |
| 工作隔离/共享库、验证门槛、提交与评审/合并授权 | 根文件；start-change及3个command |
| 经验晋升/风险例外、审计、实现diff反查 | 根文件+workflow规则 |

## 行为说明对齐

- ALL_TERMS/PHRASE来自现有keyword_match.py、search_schemas.py、analysis_crud.py，补入site-scoped-query。
- 品牌排除/隐藏类目来自analysis_crud.py与filters.ts，补入同一规格；黑名单值仍只维护在代码中。
- 日视图7日变化率来自columns.tsx与既有测试，将旧“日周都对上期”规格改为按视图定义。
- 不改应用代码；上述对齐仅恢复原指令已经描述的行为，不升级为新功能或验收结论。

## 验证结果

- 根文件433→87行，18937→4058 Unicode字符；9份规则合计236行，每份13～39行，均有paths且无@全量导入。
- 原导航前身1454→168行，仅保留规格/design/操作引用，不再维护功能正文。
- 基准320个非空原段落均登记去向；历史原文可由fe2870a复原，未再存一份手册。
- Ruby YAML解析9份frontmatter通过；9个实际存在的代表文件按glob命中，前端令牌清理跨域匹配另核对。
- 26个Markdown文件本地链接无缺失；新文件未被Git忽略；AGENTS专用Code Review Rules正文与基准完全一致。
- `openspec validate --strict --all --no-interactive`：37 passed，0 failed。
- 复用已有Python环境运行 `pytest test/test_conventions.py -q`：15 passed（0.31秒）。
- `git diff --check`通过；主工作区仍为精简前433行，未覆盖其他会话资源。
- 未修改应用代码/数据库/环境；原生加载只验证文件格式与代表路径，不冒充工具实际加载验证。
未执行：Claude Code原生加载/跨Agent实际使用、业务全量、生产和真机验收。

## 第一批可复用Skill补充与验证

- 更新local-db、verify-import；新增verify-analysis-contract及按需检查路由、diagnose-v2。根CLAUDE.md补最小入口后为88行。
- 使用现有Python环境执行skill-creator的quick_validate.py，4个Skill均通过；local-db追加非空变量守卫后单独复验通过。
- 5个Skill/参考文档本地链接全部有效；引用的25个测试/脚本路径均存在；新文件未被Git忽略；通过AST核对run_import关键字参数签名。
- `openspec validate --strict --all --no-interactive`：37 passed，0 failed；`git diff --check`通过。本次仅改文档，未重跑业务测试。
- 人工静态流程走查：共享库占用时停止且不杀连接/重建；数据库用例全skip时报告未验证；新站首期保持不可判断；直接seed不能替代CSV导入；匹配变更组合计数/导出/前端验证；HTTPS可达而SSH失败时分别报告可达与内部未验证，不运行发消息/写探针或重启。六项均在流程中有对应处理。
- 上述为包结构、路径、代码入口与人工静态流程核对，不代表Skill已被Claude Code实际加载或由Agent端到端运行。未访问生产、运行数据库测试、执行外部探针或发送消息。
