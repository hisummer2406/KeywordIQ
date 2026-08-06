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

**顺手扫一遍孤儿库**（十秒，见第 4 节的道理）：

```bash
for db in $(docker exec pgbase psql -U postgres -tAc \
      "SELECT datname FROM pg_database WHERE datname LIKE 'keywordiq_%' \
       AND datname NOT IN ('keywordiq_test','keywordiq_v2_dev')"); do
  id=${db#keywordiq_}
  # 库名只能用下划线，变更 id 用连字符 —— 两种写法都要试，否则会把活库判成孤儿
  if git show-ref --verify --quiet "refs/heads/change/$id" \
  || git show-ref --verify --quiet "refs/heads/change/${id//_/-}"; then
    echo "$db  ← 分支还在，别动"
  else
    echo "$db  ← 分支没了，是孤儿，按第 4 节删掉"
  fi
done
```

**这个下划线/连字符的坑是实测踩出来的**：第一版只按库名原样查分支，把
`keywordiq_redesign_day_week_window_view` 判成了孤儿，而它对应的
`change/redesign-day-week-window-view` 分支不但活着，还正开着 worktree。
照着删就是把别人干到一半的库端了，且对方只会看到「集成测试忽然全跳过」。

**`openspec list` 只说明变更存在，不说明有人在实现它。** 判据是分支：变更目录可以
是三天前建的提案，而 `change/<id>` 分支意味着此刻有人在写代码。

- 分支已存在 → **停下来问**，不要另起炉灶。可能是别人在做，也可能是同一个人的旧分支。
- 分支不存在 → 立刻建分支占位（下一步），别先写代码再建。占位晚了等于没占。

## 2. 建 worktree（仓库外）

```bash
ID=<变更id>                        # 与 openspec/changes/<id> 同名，通常带连字符
DB=keywordiq_${ID//-/_}            # 库名只能用下划线，这里定死换算，后面一律用 $DB
git worktree add -b "change/$ID" ~/.claude-worktrees/$ID v2
cd ~/.claude-worktrees/$ID
```

`$DB` 这一行别省。手工把连字符换成下划线的话，收工那条 `dropdb` 和第 1 节的孤儿扫描
就得各自再猜一次名字——两处猜法只要不一致，库就会被漏下或被误删。

**路径必须在仓库外。** 放 `.claude/worktrees/` 的代价是每个 watcher 都得单独配排除：
曾有 worktree 让 uvicorn 的 WatchFiles 把 70 多个文件判为源码变更，连触发 9 次全量
重载后 reloader 退出（退出码 144，日志显示优雅关闭，极易误判成应用故障）。

进去之后**全程不要 `cd` 回主仓**。跨仓提交（`cd 主仓 && git commit`）是门禁重点
盯防的形态，也是历史上漏过一个未用 import 的原因。

### 新 worktree 是空壳，三样东西不随分支走

被 `.gitignore` 的东西不在 git 里，所以**不会**跟着 worktree 过来。不补齐的话，
报错信息都指不到真正的原因：

```bash
cp /Users/stone/Developer/KeywordIQ/.env ~/.claude-worktrees/$ID/.env   # ① 配置
cd ~/.claude-worktrees/$ID/frontend && pnpm install --prefer-offline    # ② 前端依赖
docker exec pgbase createdb -U postgres "$DB"                            # ③ 本 worktree 的库
```

| 缺什么 | 报错长什么样 | 为什么难认 |
|---|---|---|
| `.env` | `ValidationError: 7 validation errors for Settings`（APP_NAME 等 required） | 像是配置类写坏了，其实只是没读到文件 |
| `frontend/node_modules` | `sh: tsc: command not found` | 像是环境坏了，其实是依赖没装 |
| 数据库 | 集成测试**静默全跳过** | 最危险的一个：测试报绿，而跳过的正是唯一碰真库的部分 |

第三条尤其要当心：`317 passed` 与 `265 passed, 52 skipped` 长得很像。**worktree 里也要有
一个能跑集成测试的库**，否则整个变更期间你都在拿单元测试的绿色自我安慰——本项目就发生过
"模型改了、查询层没跟上，单元测试全绿"，接上真库后立刻红 21 个。

库名带上变更 id（`keywordiq_<变更id>`），不要共用 `keywordiq_test`——见下一节。

### 为什么是「一个变更一个库」，而不是一个库分 schema

这个问题被问过，评估结论是：**今天做不到，且失败方式是静默的**。两条各自都足以否决：

1. **裸 SQL 里硬编码了 `analysis.`**。`importer.py` 有 5 处、`import_service.py` 有 1 处
   （`INSERT INTO analysis.site_data_stats`），另有 2 个手写迁移。查询层（`analysis_crud.py`）
   倒是走 `settings.DATABASE_SCHEMA` 的——**恰恰是这种一半参数化、一半写死的状态最坑**：
   改 `DATABASE_SCHEMA` 之后查询看着正常，导入却仍然写进 `analysis`。两个会话于是共用一份
   事实表，而各自的夹具还会 `TRUNCATE` 它——比现在更糟，且没有任何报错。
2. **咨询锁是按库隔离的，不按 schema**（实测：同一个 key 能同时被 `keywordiq_test` 与
   `keywordiq_v2_dev` 各持一份）。导入用 `pg_try_advisory_lock` 串行化，同库不同 schema 的
   两个会话会抢同一把锁，表现是集成测试随机报「导入被拒 / QUEUED」——`local-db` skill 里
   已经把这类偶发失败列为最难诊断的一档。

要改成 schema 隔离，前置是把那 6 处裸 SQL 与 2 个迁移参数化、并让锁 key 随 schema 派生。
那是一个独立变更，不是改这份 skill 能解决的。**在此之前，一个变更一个库是有依据的选择，
真正要治的是「用完不删」——那是第 4 节的事。**

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

门禁会拦掉其中一类：索引与工作树对同一文件不一致时（暂存后又改了 / 暂存后删了），
裸提交直接被拒——那种情况下门禁自己跑的检查读的是磁盘，描述的不是被提交的内容。
但**索引里那些与你无关、内容又和磁盘一致的文件门禁看不出来**，只有你知道它们不属于
这次提交，所以索引列还是得自己看一眼。

合回主分支之前**先停前端 dev server**：Umi 的 MFSU 缓存会在冲突标记还在文件里时
编译失败，冲突解完后浏览器手上仍是失败那次的产物——页面全白、控制台无异常、
tsc/eslint/vitest 全过。停掉再起即可，但不知道这条会排查很久。

清理**三样一起做完，并当场核对**——漏掉任何一样都不算收工：

```bash
git worktree remove ~/.claude-worktrees/$ID
git branch -d "change/$ID"      # -d 不是 -D：没合并进去的会被拒绝，这是保护
docker exec pgbase dropdb -U postgres --if-exists "$DB"           # 第 2 节建的专属库

# 核对：这三条都必须是空的
git worktree list | grep "$ID"
git branch --list "change/$ID"
docker exec pgbase psql -U postgres -lqt | cut -d'|' -f1 | grep -w "$DB"
```

drop 之前照例先看有没有人连着（同第 3 节）：专属库理论上只有你在用，但 dev server
误连过去、或者你自己还开着一个 psql，都会让 drop 失败或打断正在跑的东西。

**收工清理是尽力而为，不是保障——真正的兜底在第 1 节的孤儿扫描。** 会话不是每次都
善终：崩了、被关掉、或者干到一半换了方向，wrap-up 就永远不会执行，那台库就此留下。
2026-08-06 的 `keywordiq_redesign_day_week_window_view` 正是这么来的——闲置了一天多、
0 连接、64 MB，没人知道还要不要。**能清理它的只有下一个会话**，所以每次开工都扫一遍。

判据用分支而不是时间：`change/<id>` 分支还在 = 那个变更还活着，库别动；分支没了而库还在
= 孤儿，删。用「超过 N 天」当判据会误杀跨天的长变更。

**`git worktree remove` 会拒绝有未跟踪文件的工作区，这是保护不是障碍。** 遇到时先看那是
什么再决定——曾有一个 worktree 里躺着一份从未进过 git、主仓也没有副本的业务文档
（报价单 xlsx），`--force` 会连它一起删掉。先挪出来，再 remove。

专属库不删会一直攒着：每个变更留一个几 GB 的库，磁盘很快见底，而且没人记得它们是干嘛的。

## 反例（都真实发生过）

| 做法 | 后果 |
|---|---|
| 只跑 `openspec list` 就动手 | 与另一个会话把同一个变更各做一遍 |
| 裸 `git commit` | 把别人 staged 的探针文件提进主干 |
| 无条件 `dropdb keywordiq_test` | 打断别人正在跑的集成测试 |
| worktree 建在 `.claude/worktrees/` | uvicorn 重载风暴，reloader 退出 |
| 合并时开着前端 dev server | 冲突标记编进产物，页面白屏而测试全绿 |
| 从 worktree `cd 主仓 && git commit` | 门禁查空暂存区后放行（现已由 `target_repo` 堵上） |
| worktree 里不配数据库就开工 | 集成测试静默全跳过，"模型改了查询层没跟上"一路绿到接上真库才红 21 个 |
| `git worktree remove --force` 图省事 | 删掉了工作区里唯一一份未进 git 的业务文档 |
| 收工不删专属库 | 每个变更留一个几 GB 的库，攒到磁盘见底且无人知道来历 |
| 只在收工时清理，开工不扫孤儿 | 会话没善终 → wrap-up 从不执行 → 库无限期留着（真实：闲置一天多的 64 MB 孤儿库） |
| 想当然改成「一个库分 schema」 | 导入侧裸 SQL 写死 `analysis.`，两个会话共用同一份事实表并互相 TRUNCATE，且无任何报错 |
