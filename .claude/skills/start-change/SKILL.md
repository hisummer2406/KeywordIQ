---
name: start-change
description: 开始/接手一个 openspec 变更时用。先认领防止与并行会话重复劳动，再建仓库外的 worktree 隔离索引与工作树，收工时按路径提交并清理。当要动手实现某个变更、或不确定有没有人已经在做同一件事时使用。
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
```

**`openspec list` 只说明变更存在，不说明有人在实现它。** 判据是分支：变更目录可以
是三天前建的提案，而 `change/<id>` 分支意味着此刻有人在写代码。

- 分支已存在 → **停下来问**，不要另起炉灶。可能是别人在做，也可能是同一个人的旧分支。
- 分支不存在 → 立刻建分支占位（下一步），别先写代码再建。占位晚了等于没占。

## 2. 建 worktree（仓库外）

```bash
ID=<变更id>                        # 与 openspec/changes/<id> 同名
git worktree add -b "change/$ID" ~/.claude-worktrees/$ID v2
cd ~/.claude-worktrees/$ID
```

**路径必须在仓库外。** 放 `.claude/worktrees/` 的代价是每个 watcher 都得单独配排除：
曾有 worktree 让 uvicorn 的 WatchFiles 把 70 多个文件判为源码变更，连触发 9 次全量
重载后 reloader 退出（退出码 144，日志显示优雅关闭，极易误判成应用故障）。

进去之后**全程不要 `cd` 回主仓**。跨仓提交（`cd 主仓 && git commit`）是门禁重点
盯防的形态，也是历史上漏过一个未用 import 的原因。

## 3. 干活期间：三样共享资源要探活

worktree 隔离的是索引与工作树，**这三样它管不着**：

**测试库** —— `keywordiq_test` 全局只有一份。drop 前先看有没有人在用：

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

## 4. 收工

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

合回主分支之前**先停前端 dev server**：Umi 的 MFSU 缓存会在冲突标记还在文件里时
编译失败，冲突解完后浏览器手上仍是失败那次的产物——页面全白、控制台无异常、
tsc/eslint/vitest 全过。停掉再起即可，但不知道这条会排查很久。

清理：

```bash
git worktree remove ~/.claude-worktrees/$ID
git branch -d "change/$ID"      # -d 不是 -D：没合并进去的会被拒绝，这是保护
```

## 反例（都真实发生过）

| 做法 | 后果 |
|---|---|
| 只跑 `openspec list` 就动手 | 与另一个会话把同一个变更各做一遍 |
| 裸 `git commit` | 把别人 staged 的探针文件提进主干 |
| 无条件 `dropdb keywordiq_test` | 打断别人正在跑的集成测试 |
| worktree 建在 `.claude/worktrees/` | uvicorn 重载风暴，reloader 退出 |
| 合并时开着前端 dev server | 冲突标记编进产物，页面白屏而测试全绿 |
| 从 worktree `cd 主仓 && git commit` | 门禁查空暂存区后放行（现已由 `target_repo` 堵上） |
