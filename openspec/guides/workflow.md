# 开发流程与历史依据

本指南是根目录 [CLAUDE.md](../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。
文中代码与命令路径均相对仓库根目录；主文件规定跨域红线，本指南保留该域的细则、理由和历史证据。
历史测量、上线状态与人工验证记录只证明记录时状态，不替代本次验证。

## 流程重量：先问，别默认最重

**开工先问一句这件事要多重，默认取最轻的那档。** 三档：

| 档位 | 做什么 | 适用 |
|---|---|---|
| **直接做** | 不写 spec、不建 worktree，改完跑相关测试就提交 | 改样式、修一处逻辑、补一个测试 |
| **先对齐再做** | **一次**问清关键决策，复述一遍就动手；不写 spec | 需求有分叉但形态清楚 |
| **完整流程** | openspec 四件套 + worktree + PR | 新子系统、改契约、要长期留档的决策 |

判据是**这个决策以后还要不要被翻出来看**，不是改动大小。「账号按 unionId 还是 userid 匹配」
值得留档（改错了没有任何报错），「登录页加个按钮」不值得。

### 为什么要有这条

2026-08-19 的钉钉 SSO 变更（issue #36）事后统计：生产代码 949 行、测试 1106 行、
**我写的散文 1366 行**（openspec 672 + 提交信息 337 + CLAUDE.md 100 + PR/issue 正文 257）。
散文比代码多。

原因不是哪一步做错了，而是**默认把所有重流程叠满**：brainstorming 判成 architectural →
完整问答 + 方案对比 + 分段设计 → openspec 四份产物 → start-change 建 worktree 装依赖 →
TDD 逐函数红绿 → 每次提交跑全量门禁。每层单独看都合理，叠起来是写第一行代码之前先过一小时。
而这个判断全程由 agent 做，人没有机会说「不用这么重」。

### 四条具体约束

1. **澄清问题一次问完。** 一个 `AskUserQuestion` 最多带 4 个问题，**不要串行问 4 轮**——
   每轮都是一次完整的人机往返。superpowers 的 brainstorming skill 规定「一次一个问题」，
   这条与它冲突时**以本文件为准**（用户指令优先于 skill）。

2. **篇幅按「反事实是否显然」定，不要一律拉满。** 领域指南这种「解释关键约束不这么做会怎样」
   的密度，是给**会被反复翻阅的约定**用的。一次性脚本、测试辅助函数、`catch` 这类改动
   不需要。提交信息 3–5 行说清「改了什么、为什么」即可；只有当「为什么这么写」不显然、
   且写错了不会报错时，才值得展开。

3. **先验前提，再设计。** 有没有那个租户 / 权限 / 数据源 / 收不收费——先问或先查。
   SSO 那次先按 IDMesh + OIDC 出了完整方案，事后才发现收费情况查不实，整段推翻重做。
   同理排查缺陷时**先读失败的用例源码，再提假设**：看见 `findByText` 超时就去调超时，
   改完还是红，回头才发现那条用例用的是同步 `getByRole`，跟超时无关。

4. **测试按影响面跑，全量只在提交前一次。** 前端全量约 97 秒且随用例数增长；
   `pnpm test src/pages/Login` 是 7 秒。`pnpm exec vitest related <file>` 走真实模块图，
   对叶子文件能省 9 倍——但 `.umirc.ts` / `vitest.config.ts` / `src/test/setup.ts` 这类
   **不在模块图里却影响所有测试**的文件它会匹配出 0 个，那种必须跑全量。

## 跨 Agent 交付流程：Issue 是交接契约，PR 是审核包

默认由用户在 **Codex 桌面端**讨论需求；讨论收敛后，先创建 GitHub Issue，再由
**Claude Code** 实现、**Codex** 审核。工具的分工不是约束用户必须在哪个界面思考，而是让
需求作者、实现者与审核者各自有清晰的事实来源：**不要要求下一个 agent 重读上一段完整聊天**。

完整流程如下；小改动仍按上一节的「直接做」或「先对齐再做」分流，不为流程而流程：

```text
Codex 桌面端讨论 → GitHub Issue → Claude Code：OpenSpec + worktree + 实现
→ PR + CI → Codex 审核 → Claude Code 修复 → Codex 复审通过
→ 合并至 v2 → 手动关闭 Issue + 删除分支/worktree
```

### 1. Issue：跨工具的唯一交接物

Issue 不得只有标题或聊天摘要；至少写明**背景与目标、可验证的验收条件、非目标、已决定的
关键口径/约束**，以及仍需在 OpenSpec 中落定的决策。这样实现者读取 Issue、仓库约定与
OpenSpec 即可开工，不依赖任何未共享的会话上下文，也避免把整段聊天、命令日志复制进去消耗
上下文。

### 2. 实现与 PR：把契约和证据一并交付

Claude Code 先按 Issue 判断流程重量；需要长期留档的变更走 OpenSpec，并遵守下文的认领与
仓库外 worktree 规则。PR 是给审核者的完整审核包，必须链接 Issue，并包含：对应的
`openspec/changes/<id>`、本次 spec delta、实现摘要、已运行验证及其结果、已知未覆盖风险。

#### Claude Code 代发 `@codex review`

Codex 评审由 PR 下的 `@codex review` 评论触发（GitHub 上的 Codex 应用响应，评论作者是用户自己的
GitHub 账号）。**用户在对话里明确要求时（如「让 codex 评审」「再发一轮复审」），Claude Code 直接代发**，
不必让用户打开网页；没有这类明确要求时仍不自行发送——评审轮次由用户把控，`/deliver-pr`、
`/fix-review` 的调用本身不构成授权，授权也只对当次要求有效，不延伸到之后的轮次。

代发前先核对：PR 存在且 base 为 `v2`；本地提交已推送，PR 的 `headRefOid` 等于要评审的提交；
该 SHA 的 CI 已有结果（红灯先报告，由用户决定是否仍发）。发送用：

```bash
gh pr comment <PR号> --body "@codex review"
```

可在评论里补一两行"请重点看"的范围，不贴聊天或日志。发完只报告评论链接，不轮询等待评审结果
（用户之后再调 `/fix-review` 处理）。`@claude` 触发评论仍不代发。

### 3. 审核与修复：独立视角，但不越权改分支

Codex 审核 Issue / OpenSpec / PR diff 是否一致，并检查数据正确性、权限边界、迁移与测试缺口。
审核在独立的只读 checkout 或 worktree 中进行，**不得直接改 Claude Code 正在开发的 worktree
或 PR 分支**。发现分为「必须修复」「建议」「疑问」；Claude Code 对每项必须修复逐条修复，
或说明为何不修复并让审核者确认。只有所有必须修复关闭、相关 CI 与验证通过，才是审核通过。

### 4. 合并与收尾：v2 的 Issue 要手动关闭

审核通过后才合并进 `v2`。GitHub 只会在合并到默认分支时处理 `Closes #...`，而默认分支是
`main`，所以合进 `v2` 后必须**手动关闭 Issue**。确认合并后，才按下文的收工流程删除远端
分支与 worktree；评审未结束前保留它们，给 CI 红灯和后续意见留出修复位置。

## 工作分支是 `v2`，不是 `main`

V1 已完成使命并退役，V2 是完整重建（见根目录 `CLAUDE.md`「V2 数据模型」）。**所有工作基于 `v2`**，
`main` 停在 V1 最后的状态不动，等 V2 整体做完再一次性合并。

两条直接后果：

- **开分支从 `v2` 拉**（`git worktree add -b "change/$ID" ... v2`）。
- **`gh pr create --base v2` 的 `--base` 不可省。** 仓库的**默认分支是 `main`**，漏掉它会
  开出一个含上百提交、根本没法读的 PR。开完立刻 `gh pr view` 核一眼 base 对不对。

同理，`Closes #<n>` 在合并进 `v2` 时**不会触发关闭**（GitHub 只在合并进默认分支时关 issue），
需要手动 `gh issue close`。

**V2 不逐个发布**：各变更合进 `v2` 后不单独上线，整体做完再一次性发布。列剩余工作时不要
夹带发布步骤。V2 环境与手动触发的发布工作流已经存在（见「Production Environment」），所以
这条现在的含义是**「不主动发起发布」**，不是「发不了」——是否发布由用户决定。

**用户在对话里明确要求发布时（如「发布」「部署到服务器」），Claude Code 直接代为触发**
`Deploy V2 to ECS`，不必让用户打开网页（2026-10-21 用户授权，与上文代发 `@codex review`
同一模式）。授权只对当次要求有效，不延伸到之后的发布；`/close-change` 合并完成、PR 评审
通过都不构成发布授权。触发前先核对并向用户报告：

- 目标 SHA 是 `origin/v2` 最新提交，本地无未推送提交；
- 该 SHA 的后端/前端/插件三项 CI 全部成功（红灯或尚无 check-runs 先报告，由用户决定，
  不要硬触发）；
- 自上次部署以来是否含新的 Alembic 迁移——**数据库没有自动备份**，有迁移时要在触发前
  明说，等用户确认；
- 当前没有进行中的导入批次。

触发用 `gh workflow run "Deploy V2 to ECS" --ref v2`，随后 `gh run watch` 到结束，
报告工作流结论与 `/health` 探活结果。失败只报告现象与日志要点，**不自行重试，也不自行
SSH 到 ECS 修补**。

## 并行会话：一个变更一个 worktree

**这个仓库经常同时有多个会话在干活。** 默认假设不是「只有我一个人」。
开工流程见 skill **`start-change`**（认领 → 建 worktree → 收工），下面是它背后的道理。

### 一个变更一个 worktree，路径在仓库外

每个 openspec 变更配一个分支 `change/<变更id>` 和一个 worktree，路径
`~/.claude-worktrees/<变更id>`——**不要放 `.claude/worktrees/`**。放仓库内，每个
watcher（uvicorn `--reload`、webpack、pytest 收集）都得单独配排除，漏一个就出事：
曾有 worktree 让 WatchFiles 把 70 多个文件判为源码变更，连触发 9 次全量重载后
reloader 直接退出（退出码 144，日志是优雅关闭，极易误判成应用问题）。放仓库外，
所有 watcher 都看不见它。

worktree 解决的是**共享索引与共享工作树**这两类碰撞：

- `git commit` 提交的是整个索引，别的会话 staged 的文件会被一并带走（真实发生过）
- 文件在 Read 与 Edit 之间被另一个会话改写，编辑直接失败

### worktree 解决不了的三类，要另外守

1. **重复劳动。** 两个 worktree 不阻止两人做同一件事，只会把撞车从「改到一半」
   推迟到「合并时」。`openspec list` 只说明变更**存在**，不说明**有人在写代码**——
   判据是分支：`git branch --list 'change/*'`。开工前查，开工后立刻建分支占位。
2. **共享测试库。** `keywordiq_test` 是全局的一份。**drop 前必须先查活动连接**
   （`pg_stat_activity`），无条件 `dropdb` 会把别人正在跑的集成测试拦腰打断，
   而对方看到的是一堆无从解释的报错。

   **反过来也成立：跑一次全量 pytest 会清空这个库里的业务数据。**
   `test_import_service_integration.py` 与 `test_oss_ingestion_integration.py` 的夹具在
   每个测试后 `TRUNCATE analysis.fact_daily, fact_weekly, dim_keyword, dim_product,
   site_data_stats, import_batch RESTART IDENTITY CASCADE`——它们测的是 `run_import`，
   而后者内部提交，回滚兜不住，只能靠 TRUNCATE 收尾。所以**别往 `keywordiq_test` 里放
   你还要用的数据**：`verify-import` 灌进去的真实报表，下一次 `git commit`（门禁会跑
   全量）就没了，且没有任何提示。要留着就换个库名。

   同理，集成测试里的计数**必须带作用域**（`WHERE site=... AND report_date=...`）。
   裸 `count(*) FROM analysis.fact_daily` 只在"库里除了我什么都没有"时成立，一旦有别
   的会话留下的已提交数据就会失败，而报出来的 diff 完全看不出是这个原因。
3. **共享端口。** 只有主仓跑 dev server（8000/8001）。worktree 里要跑就从变更 id
   派生端口，不要抢。dev server 被别人杀掉或抢走时，先确认是不是并行会话所致。

### 提交前看索引列

`git status --short` 的**第一列是索引**。看到不属于本次改动的条目，用
`git commit -- <路径>` 按路径提交，不要裸 `git commit`——后者提交整个索引。
这条与 worktree 无关，是操作纪律，全面 worktree 化之后同样要守。

**门禁会拦一半。** 索引与工作树对同一文件不一致时（暂存后又改了、或暂存后从磁盘
删了），裸 `git commit` 被直接拒绝——因为门禁的 ruff / pytest 读的是磁盘，而裸提交
提交的是索引，两者不一致时那份"跑绿了"的验证描述的不是这次提交的内容。历史上一个
含未用 import 的文件正是这样进的主干：它被暂存后从磁盘删掉（索引状态 `AD`），
ruff 根本看不见它。`git commit -a` 与 `git commit -- <路径>` 提交的都是工作树内容，
与被 lint 的那份一致，故不受此限。

门禁**拦不住**的那一半：索引里那些与你无关、内容又和磁盘一致的文件（别的会话暂存
好的）——它们看起来完全正常，只有你知道它们不属于这次提交。所以索引列还是得自己看。

> **「暂存区是空的」这件事本身漏过两次，成因不同。** 一次是查错了仓库：会话在
> worktree 里往主仓提交，门禁查 worktree 的空暂存区，查了个空就放行——现已按**提交
> 实际落地的仓库**取（`target_repo` 解析 `cd X && git commit` 与 `git -C X commit`），
> 不按会话 cwd。另一次是查错了地方：`git commit -- <路径>` 提的是工作树内容，暂存区
> 可以完全是空的，而 `staged_files` 当时只在带 `-a` 时才补看工作树——于是本节推荐的
> 这条提交方式恰恰是门禁覆盖不到的那条（PR #66 的文案修复那次，20 个文件一条检查
> 没跑）。现已让路径式提交与 `-a` 走同一条路。
>
> 两次的共同表现是**没有失败、也没有拦截，输出与"全部检查通过"一模一样**。所以
> 门禁自己的回归测试（`test/test_pre_commit_hook_unit.py`）里，`staged_files` 那一组
> 守的不是"检查会不会失败"，而是"门禁到底看不看得见有改动"。

## 开发 harness（提交门禁）

**开工先 `openspec list`。** 手上的活儿很可能正是某个在飞变更里没勾完的任务，
先看一眼比做完才发现便宜得多。

### 提交门禁

`git commit` 之前会自动按改动范围跑验证，不通过就拒绝提交。挂在 `PreToolUse` 上
（`.claude/settings.json` → `.claude/hooks/pre_commit.py`），**只卡提交、不卡收尾**——
改到一半停下来问问题是正常节奏，不该被打断；提交则是「我认为这份改动可以了」的声明，
在那里要求它是绿的没有歧义。

| 改动范围 | 跑什么 | 耗时 |
|---|---|---|
| `app/` `scripts/` `test/` `alembic/` 及顶层 `.py` | `ruff check .` + `pytest test/ -q` | ~5s |
| `frontend/` | `pnpm lint` + `pnpm tsc` + `pnpm test` | ~30s（其中 vitest 占 ~28s） |
| `extension/` | `pnpm run typecheck` + `pnpm run test`（在 `extension/` 下） | ~5s |
| 只有文档 / `openspec/` | 放行 | — |

**这些检查并发跑**（`run_checks`），墙钟是最慢那条而不是各条之和：前后端都改时实测
从 51 秒降到 42 秒。省不到"总和减最大值"那么多，因为它们要抢 CPU——vitest 本来就按
核心数起 worker，旁边再跑 pytest 会把它自己拖慢几秒。**失败报告按传入顺序排，不按完成
顺序**：否则同一份改动两次提交会得到不同顺序的报告，读的人无从判断是不是同一批失败。

本地 `keywordiq_test` 连得上时会自动带上 `TEST_DATABASE_URL` 解开集成测试，连不上就
只跑单元部分（不因此拒绝提交，CI 有真库兜底）。判据是**真连一次库**而不是探端口——
本机跑着别的 PostgreSQL 时端口一样是通的，据此喂下去会让集成测试整片失败。

每条检查有 300 秒超时，超时按失败处理。卡住的 pytest（抢咨询锁、半开连接）若没有
上限，表现是「agent 不动了」而不是「检查没过」，比失败难诊断得多。门禁自身的回归在
`test/test_pre_commit_hook_unit.py`。

### 铁律检查在哪

分两处，**不重叠**：

- **ruff**（`pyproject.toml` 的 `[tool.ruff.lint]`）——能靠「看一个文件」判定的：
  事件循环上的阻塞调用（`ASYNC` 组）、未用导入、`banned-api` 拦下的 Pydantic v1 /
  SQLAlchemy 1.x 导入。
- **`test/test_conventions.py`**——ruff 表达不了的：配置项与文档的一致性、死配置、
  `except` 块里丢栈的日志。跑 `uv run pytest -m conventions`，亚秒级、不连库。

新增铁律前先问「ruff 能不能查」，能就别手写。手写的规则**必须自带正反 fixture**
（违规样本要被抓到、干净样本不能误报）——检查器自己是没人测的检查器，这正是它烂掉的方式。

### 基线棘轮

规则建立之前就存在的违规登记在 `test/conventions_baseline.toml`，被放行，好让门禁
今天就是绿的。规则同时保证两件事：**新增违规会失败**，且**基线项一旦修好、测试会
要求你把它从基线删掉**（否则基线会腐烂成永久豁免）。

**基线当前是空的**——建立规则时登记的三笔债已全部清偿。空基线是常态，不是终点：
下次有规则拦不住又暂时修不了的历史问题，照样往里登记，只要它继续满足「新增会红、
修好要删」这两条。

历史债务（返值式错误、上传死配置、连接池漏算）已清偿，旧参数不作为当前配置依据。
当前池预算见 [生产边界与容量](operations.md)，配置默认值见根目录 `.env.example` 与 `config.py`。
新增违规仍须失败，基线项修好后必须删除登记；空基线不是永久豁免。

### 项目 skill

- **`start-change`** —— 开始或接手一个变更时先跑它：认领（防止与并行会话重复劳动）、
  建仓库外的 worktree、收工时按路径提交并清理。**动手写代码之前用**，见上文
  「并行会话」一节。
- **`local-db`** —— 起测试库、跑含集成测试的全量回归。注意 `225 passed` 与
  `181 passed, 45 skipped` 长得很像，跳过的 45 个恰是唯一碰真实分区表与咨询锁的部分。
  **drop 测试库前先查 `pg_stat_activity`**，别人可能正在用同一个库。
- **`verify-import`** —— 用真实体量的报表 CSV 跑一遍端到端导入并逐项断言落库结果。
  改过导入路径就跑一次。

### 项目 command（手动调用）

交付、修复与合并由用户分别触发；流程文件放在 `.claude/commands/`，不另建同名 skill。

- **[`/deliver-pr`](../../.claude/commands/deliver-pr.md)** —— 按约定检查、commit、push，
  创建或更新指向 `v2` 的 PR，保留分支/worktree，等待用户手动 `@codex review`。
- **[`/fix-review [PR号]`](../../.claude/commands/fix-review.md)** —— 读取 Codex 的普通评论、
  正式 review 与行内意见，逐项核实、修复、红绿验证、提交、回复并核对当前 SHA 的 CI。
  不自动触发复审或合并；下一轮由用户再次调用。GitHub 中用 `@claude` 明确要求执行该文件。
- **[`/close-change [PR号或变更ID]`](../../.claude/commands/close-change.md)** —— 审核和 CI
  已通过才合并至 `v2`，转移仍需人工验收的任务、归档 OpenSpec 并推送归档结果，
  按完成范围手动关闭 Issue，最后清理本变更已合并的分支及干净 worktree。
  不等待审核、不强制清理、不触碰其他会话资源；已合并的 PR 可重跑以完成剩余收尾。

## 当前部署交接

V2 ECS、OSS 与飞书运维机器人的实时交接状态统一记录在
[`docs/ops/v2-ecs-handoff.md`](../../docs/ops/v2-ecs-handoff.md)。开始任何部署、回调或运维变更前先阅读它；该文件不得记录任何密钥。
