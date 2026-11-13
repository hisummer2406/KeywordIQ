---
name: start-change
description: 开始/接手一个 openspec 变更时用。先认领防止与并行会话重复劳动，再建仓库外的 worktree 隔离索引与工作树，收工时按路径提交、开 PR 回 v2、合并后清理。当要动手实现某个变更、或不确定有没有人已经在做同一件事时使用。
---

# 开一个变更：认领 → 隔离 → 收工

**这个仓库经常同时有多个会话在干活。** 这个 skill 存在的原因是：2026-08-04 一天之内
两个会话把同一个变更（周视图搜索趋势）各做了一遍，互相修了同一处测试断言；一次
`git commit` 把另一个会话 staged 的探针文件卷进了主干；一次无条件 `dropdb` 差点打断
别人正在跑的集成测试。这些都不是手快手慢的问题，是缺少约定。

## 1. 认领（动手之前，不可跳过）

```bash
openspec list                      # 有哪些在飞的变更
git branch --list 'change/*'       # 谁已经在写代码了 —— 这才是判据
git worktree list                  # 谁开着工作区
gh pr list --base v2               # 谁已经写完在等评审
```

**后两条同时是孤儿扫描。** 会话不是每次都善终（崩了、被关掉、干到一半换了方向），
那时第 4 节的清理永远不会执行，worktree 和分支就此留下——**能清理它们的只有下一个
会话**，所以每次开工都顺手看一眼。判据用分支而不是时间：分支还在 = 变更还活着，别动；
分支没了而 worktree 还在 = 孤儿，按第 4 节清掉。用「超过 N 天」当判据会误杀跨天的长变更。

**`openspec list` 只说明变更存在，不说明有人在实现它。** 判据是分支：变更目录可以
是三天前建的提案，而 `change/<id>` 分支意味着此刻有人在写代码。

- 分支已存在 → **停下来问**，不要另起炉灶。可能是别人在做，也可能是同一个人的旧分支。
- 分支不存在 → 立刻建分支占位（下一步），别先写代码再建。占位晚了等于没占。

## 2. 建 worktree（仓库外）

```bash
ID=<变更id>                        # 与 openspec/changes/<id> 同名，通常带连字符
git worktree add -b "change/$ID" ~/.claude-worktrees/$ID v2
cd ~/.claude-worktrees/$ID
```

**路径必须在仓库外。** 放 `.claude/worktrees/` 的代价是每个 watcher 都得单独配排除：
曾有 worktree 让 uvicorn 的 WatchFiles 把 70 多个文件判为源码变更，连触发 9 次全量
重载后 reloader 退出（退出码 144，日志显示优雅关闭，极易误判成应用故障）。

进去之后**全程不要 `cd` 回主仓**。跨仓提交（`cd 主仓 && git commit`）是门禁重点
盯防的形态，也是历史上漏过一个未用 import 的原因。

### 新 worktree 是空壳，两样东西不随分支走

被 `.gitignore` 的东西不在 git 里，所以**不会**跟着 worktree 过来。不补齐的话，
报错信息都指不到真正的原因：

```bash
cp /Users/stone/Developer/KeywordIQ/.env ~/.claude-worktrees/$ID/.env   # ① 配置
cd ~/.claude-worktrees/$ID/frontend && pnpm install --prefer-offline    # ② 前端依赖
```

| 缺什么 | 报错长什么样 | 为什么难认 |
|---|---|---|
| `.env` | `ValidationError: 7 validation errors for Settings`（APP_NAME 等 required） | 像是配置类写坏了，其实只是没读到文件 |
| `frontend/node_modules` | `sh: tsc: command not found` | 像是环境坏了，其实是依赖没装 |

**数据库默认不用建，但要确认 `keywordiq_test` 在。** 集成测试与提交门禁默认都跑在这一个共享库上
（`pre_commit.py` 的 `TEST_DB_URL`）。**本变更带迁移、而另一个在飞分支也带迁移时**，共享库只能停在
其中一条链上，另一方的门禁必然红：这时在本 worktree 的 `.env` 写一行
`TEST_DATABASE_URL=<本机一次性库>`，门禁就改用它（只接受本机地址），建库与迁移见 `local-db`。库不在时门禁不会报错，只会让那 45 个集成测试**静默全跳过**
——`317 passed` 与 `265 passed, 52 skipped` 长得很像，而跳过的正是唯一碰真实分区表和咨询锁
的部分。本项目发生过「模型改了、查询层没跟上，单元测试全绿」，接上真库立刻红 21 个。
建库跑法见 skill `local-db`。

> **别再加回「一个变更一个库」。** 2026-08-12 删掉，因为它**从来没有被接上**：`$DB`
> 全文只出现在 `createdb` 与 `dropdb` 两处，没有任何地方把 `.env`、`TEST_DATABASE_URL`
> 或门禁指向它——建出来的是个连迁移都没跑过的空库，而真实的测试自始至终跑在
> `keywordiq_test`。它收着维护成本（孤儿扫描、下划线/连字符换算、收工 dropdb），
> 隔离性是零。门禁现在可以读 worktree `.env` 的 `TEST_DATABASE_URL`（2026-11-10），但仍只在
> 迁移链冲突时按需建库，不恢复每个变更固定建库、收工 dropdb 的流程。

## 3. 干活期间：三样共享资源要探活

worktree 隔离的是索引与工作树，**这三样它管不着**：

**测试库** —— `keywordiq_test` 全局只有一份，两条都要守：

1. **别往里放你还想要的数据。** 集成测试夹具在每个测试后 `TRUNCATE` 事实表与维度表
   （`run_import` 内部提交，回滚兜不住），而**全量 pytest 是每次 `git commit` 都跑的**
   （门禁，只要碰了 `app/` `scripts/` `test/` `alembic/` 或顶层 `.py`）。所以
   `verify-import` 灌进去的真实报表，下一次提交就没了，且没有任何提示。要留着就换个库名。
2. **drop 前先看有没有人在用**（并行会话可能正跑着集成测试）：

```bash
docker exec pgbase psql -U postgres -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='keywordiq_test'"
```

非 0 就别 drop。无条件 `dropdb` 会把别人的集成测试拦腰打断，对方看到的是一堆无从
解释的报错。跑法见 skill `local-db`。

**端口** —— 只有主仓跑 dev server（8000 / 8001）。worktree 里要跑就换端口，不要抢。
发现 dev server 没了或起不来，先怀疑并行会话，别当成应用问题排查。

**openspec 的 tasks.md** —— 长任务开始和收尾各留一行痕迹（谁、什么时候、跑到哪一步）。
并行的人只能通过它知道你做到哪了。**勾选与说明必须一致**：勾了 `[x]` 却在尾注写
「待运行」，读的人无从判断到底跑没跑。

## 4. 收工：提交 → 开 PR → 合并后清理

**收工不再是一口气。** 从前是「提交 → 本地 merge 进 v2 → 清理」，中间没有停顿；
现在中间隔着一个 PR 和一次评审，**清理必须挪到合并之后**。把它当成两个时刻：
今天把 PR 开出去，改天等它合并了再回来收拾。

### 4.1 提交

提交前**先看索引列**：

```bash
git status --short          # 第一列是索引，第二列是工作树
git diff --cached --name-only
```

看到不属于本次改动的条目（另一个会话 staged 的东西），按路径提交：

```bash
git commit -F <消息文件> -- <只属于本次的路径>
```

**不要裸 `git commit`** —— 它提交整个索引。已经误提交了就
`git reset --soft HEAD~1` 再按路径重提，索引里其余条目会原样保留。

门禁会拦掉其中一类：索引与工作树对同一文件不一致时（暂存后又改了 / 暂存后删了），
裸提交直接被拒——那种情况下门禁自己跑的检查读的是磁盘，描述的不是被提交的内容。
但**索引里那些与你无关、内容又和磁盘一致的文件门禁看不出来**，只有你知道它们不属于
这次提交，所以索引列还是得自己看一眼。

### 4.2 开 PR

先把 v2 的新提交并进分支、在**分支上**解掉冲突，别把冲突留给 PR 页面：

```bash
git fetch origin
git merge origin/v2
```

**解冲突之前先停前端 dev server**：Umi 的 MFSU 缓存会在冲突标记还在文件里时编译失败，
冲突解完后浏览器手上仍是失败那次的产物——页面全白、控制台无异常、tsc/eslint/vitest
全过。停掉再起即可，但不知道这条会排查很久。

然后推分支、开 PR：

```bash
git push -u origin "change/$ID"
gh pr create --base v2 --title "<变更标题>" --body-file <说明文件>
```

**`--base v2` 不可省。** 仓库的默认分支是 `main`，而 V2 是完整重建——不传 base
会开出一个上百提交、根本没法读的 PR。开完立刻 `gh pr view` 核一眼 base 对不对。

PR 说明里放三样，缺一样这个 PR 就不值得开：

| 放什么 | 为什么 |
|---|---|
| 变更 id（`openspec/changes/<id>/`） | 评审者据此找 proposal 与 design，知道这是在实现什么 |
| `Refs #<issue>` | issue 关掉时能追到是哪个变更做掉的 |
| 本次 spec delta 的文件列表 | **这是开 PR 的全部理由** |

**开 PR 的意义就在于 spec delta 与代码在同一个 diff 里被看见。** 代码改了而
`specs/*/spec.md` 没改，或者反过来，在一个 diff 里是一眼可见的；分开评审看不见，
本地 merge 更是压根没有这个检查点。

**别在开完 PR 后立刻清理。** 两个原因：

1. **CI 会跑你本地跑不到的东西。** `.github/workflows/ci.yml` 在 `pull_request → v2`
   上起真 Postgres，本地因为连不上库而静默跳过的那批集成测试，在这里会真跑。
   「本地绿」不等于「PR 绿」。
2. **评审意见要有地方改。** worktree 删了，改一行意见就得把第 2 节整套重来。

### 4.3 合并后清理

用**合并提交**合，不要 squash：

```bash
gh pr merge --merge --delete-branch=false
```

`--merge` 这个选择是有代价约束的：squash 会重写提交，本地 `change/$ID` 的提交
不再是 v2 的祖先，下面那条 `git branch -d` 会拒绝删——而人在那一刻的反应就是改用
`-D`，保护随之作废。历史上 `change/*` → `v2` 一直是合并提交（`0b58f4b` 即是），
沿用它就能让 `-d` 继续当那道闸。`--delete-branch=false` 是因为本地分支还要留给
`-d` 判定，远端分支等清理时一起收。

**4.3 整节在主仓做，不在 worktree 里。** 第 2 节那条「全程不要 cd 回主仓」管的是
干活期间，到这里已经不适用了：worktree 删不掉自己所在的工作区，`v2` 也已经被主仓
checkout 占着（在 worktree 里 `git checkout v2` 会直接报
`fatal: 'v2' is already checked out at ...`）。

```bash
cd /Users/stone/Developer/KeywordIQ
git checkout v2 && git pull --ff-only   # 让本地 v2 含上合并结果，跳过这步 -d 一定拒绝；PR 合在备用库时改拉对应远端
```

然后清理**三样一起做完，并当场核对**——漏掉任何一样都不算收工：

```bash
git worktree remove ~/.claude-worktrees/$ID
git branch -d "change/$ID"      # -d 不是 -D：没合并进去的会被拒绝，这是保护
git push origin --delete "change/$ID"

# 核对：这三条都必须是空的
git worktree list | grep "$ID"
git branch --list "change/$ID"
git ls-remote --heads origin "change/$ID"
```

**`git branch -d` 被拒绝时不要换 `-D`，先查为什么。** 只有两种可能：PR 还没合并
（那就等），或者被 squash 了（那是 4.3 开头那条约定没守住，此时用
`gh pr view --json state` 确认状态是 `MERGED` 再 `-D`）。凭手感 `-D` 就是把唯一
一道「别删还没合进去的东西」的闸拆了。

**收工清理是尽力而为，不是保障——真正的兜底在第 1 节的开工扫描。** 会话不是每次都
善终：崩了、被关掉、或者干到一半换了方向，wrap-up 就永远不会执行，worktree 和分支
就此留下。**能清理它们的只有下一个会话。**

**改成 PR 之后这一条只会更重要**：合并发生在开 PR 那个会话早就结束之后，4.3 那步
天生比从前更容易掉——从前它紧跟在提交后面，现在中间隔着一次评审。

**分支还在也分两种，用 `gh pr list --base v2` 区分**：开着 PR 等评审的，worktree
该原样留着（评审意见要在那儿改）；没有 PR、也没人在写的，才是有人干到一半走掉了
——那种去问，别自己接着做（第 1 节）。

**`git worktree remove` 会拒绝有未跟踪文件的工作区，这是保护不是障碍。** 遇到时先看那是
什么再决定——曾有一个 worktree 里躺着一份从未进过 git、主仓也没有副本的业务文档
（报价单 xlsx），`--force` 会连它一起删掉。先挪出来，再 remove。

## 反例（都真实发生过）

| 做法 | 后果 |
|---|---|
| 只跑 `openspec list` 就动手 | 与另一个会话把同一个变更各做一遍 |
| 裸 `git commit` | 把别人 staged 的探针文件提进主干 |
| 无条件 `dropdb keywordiq_test` | 打断别人正在跑的集成测试 |
| worktree 建在 `.claude/worktrees/` | uvicorn 重载风暴，reloader 退出 |
| 解冲突时开着前端 dev server | 冲突标记编进产物，页面白屏而测试全绿 |
| 从 worktree `cd 主仓 && git commit` | 门禁查空暂存区后放行（现已由 `target_repo` 堵上） |
| `keywordiq_test` 不存在就开工 | 集成测试静默全跳过，"模型改了查询层没跟上"一路绿到接上真库才红 21 个 |
| 把还要用的数据灌进 `keywordiq_test` | 下一次 `git commit` 跑门禁全量 pytest，夹具 TRUNCATE 掉，无任何提示 |
| `git worktree remove --force` 图省事 | 删掉了工作区里唯一一份未进 git 的业务文档 |
| 只在收工时清理，开工不扫一眼 | 会话没善终 → wrap-up 从不执行 → worktree 和分支无限期留着 |

## PR 这条腿的已知陷阱

2026-08-10 从本地 merge 改成 PR，**下面四条是推演出来的，还没攒出事故**——攒出来了
就挪进上面那张表，并把推演换成事实。

| 做法 | 后果 |
|---|---|
| `gh pr create` 不传 `--base v2` | 仓库默认分支是 `main`，会开出一个上百提交、没法读的 PR |
| PR 用 squash 合并 | 提交被重写，`git branch -d` 拒绝，人转手用 `-D`，那道闸就废了 |
| 开完 PR 立刻清 worktree 和库 | CI 的真库测试红了、或评审提了意见，没有地方改 |
| PR 里只有代码没有 spec delta | 退化成一次普通 code review，开 PR 的理由整个消失 |
