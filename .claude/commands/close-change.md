---
description: 确认 PR 审核及 CI 通过后合并至 v2、归档 OpenSpec、按条件关闭 Issue 并清理本变更。
argument-hint: "[PR 编号、URL 或变更 ID，省略则取当前 PR]"
disable-model-invocation: true
---

收尾目标：$ARGUMENTS

本 command 仅由用户明确调用，调用即授权在以下条件满足时合并、归档、提交/推送归档结果、
关闭已完整解决的 Issue，以及删除本变更已合并的分支和干净 worktree。不要重复请求这些授权。
不触发审核、不修复代码、不部署，不清理其他会话资源。

## 1. 确认目标和门槛

- 读取 `AGENTS.md`、`CLAUDE.md` 和适用的嵌套约定。参数可为 PR 编号/URL 或变更 ID；
  无参数时优先取 GitHub 当前 PR，其次本机分支关联 PR；无法唯一匹配时才询问。
  从 PR/Issue/OpenSpec 确认变更 ID，不能凭 PR 标题猜。通过评论读取本文件时，
  未替换的 `$ARGUMENTS` 按无参数处理。
- 记录 PR 的 `state`、`baseRefName`、`headRefName`、`headRefOid`、head 仓库、
  `mergeCommit`、`reviewDecision`、`mergeStateStatus`、URL，以及关联 Issue/变更目录。
  base 必须为 `v2`，状态须为 `OPEN` 或 `MERGED`；其他情况停止并报告。
- 用 `git worktree list --porcelain` 确定主工作区与本变更工作区的真实路径，不硬编码用户目录。
  主工作区须干净（含未跟踪文件），混有无关改动时只报告，不暂存、移动、删除或继续 pull/归档。
  不允许工作区删除自身，后续同步/归档/清理在主工作区进行。
- `OPEN` 时检查当前 head 的 CI 与审查证据：普通评论、正式 review、行内意见及后续回复都需
  取齐分页（端点见 `.claude/commands/fix-review.md`）。必须修复项须已关闭，不采纳项须有
  审核者确认；不能把空 `reviewDecision`、没有评论或旧提交的通过当作当前审核通过。
  `gh pr checks` 与当前 SHA 的运行结果都要核实；必需检查缺失、失败、等待或证据不明时
  报告并结束本轮，不在本 command 内等 CI、不跳过门禁。

  **例外（防止无限跟进外部复审）**：若当前 head 没有新的外部 review，但存在一条
  `/fix-review` 针对**当前 head SHA**发布的 PR 顶层评论（不是只在聊天里说过），
  且该评论同时满足——(a) 逐条列出上一轮全部 Codex 意见的核实结论（已修复/不成立
  及证据），(b) 附对应提交的 CI 结果，(c) 明确写出"不建议继续下一轮复审"这一判断
  本身（不是"建议再发一轮"或"建议收尾但仍推荐复审确认"这类保留意见）——视为
  审核证据齐备，可以跳过等待新一轮外部 `@codex review`，直接进入合并。
  这条例外**只在评论记录的提交 SHA 与当前 head 完全一致时成立**；针对更早提交的
  "建议收尾"结论不适用于之后又有新提交的 head，须先核实新提交没有引入其它待处理
  改动。评论措辞若是"建议再来一轮确认"而非"不建议再跟进"，仍按常规路径要求新的
  外部 review，不得由本 command 自行判断"应该没问题"从而放行。
- `MERGED` 时记录真实合并提交，跳过再次合并，可继续尚未完成的归档和清理。

## 2. 合并并同步

仅对审核和检查通过的 `OPEN` PR 执行，用上一步核实的 head SHA 防止并发更新：

```bash
gh pr merge <PR号> --merge --delete-branch=false --match-head-commit <已审核SHA>
gh pr view <PR号> --json state,mergedAt,mergeCommit
```

不使用 squash/rebase、`--admin` 或 `--auto` 绕过条件。结果不是 `MERGED`（包括排队中）
就停止，不清理。在干净主工作区同步 `v2`：

```bash
git switch v2
git pull --ff-only origin v2
git merge-base --is-ancestor <合并提交> HEAD
```

检查最后一条的**退出码为 0**，不能用“没有输出”判断成功。失败则停止，不强制重置本地分支。

同步前先看主工作区是否有本项目 dev server 在跑（`lsof` 查 cwd）；`package.json`/`pyproject.toml`
或锁文件在此次拉取中有变化时，同步后在 `frontend` 重新 `pnpm install`（后端用 `uv sync`），
否则本地页面会因依赖不匹配而坏。PR 合在备用库时，从备用库对应远端同步，两仓 v2 分叉就报告。

## 3. 处理验收并归档

- 有 OpenSpec 变更时归档必做；无变更目录的小改动明确记为不适用，不虚构 spec。
  已归档则核实归档和主 specs 已在远端，不重复归档或制造空提交。
- 归档前只勾选实际完成的任务。仍有无法自动验证的人工验收项时，搜索现有活跃验收清单 Issue，
  按“页面/账号类型/观察行为”追加简短 checklist，记录原任务与目标链接；已有记录不重复追加。
  找不到合适的 Issue 时问一次去处，不让未完成项随归档消失；剩余代码任务不能冒充人工验收。
- 标明人工项已转移后执行：

```bash
openspec archive <变更id> -y
openspec validate --strict --all
```

  若提示旧场景标题缺失，先核对是删除还是改写；改写时保留对应旧标题再重试，不能盲目绕过。
- 核对归档产生的 archive 目录、原目录删除与主 specs 差异，只按本次路径提交，
  不用 `git add openspec/` 卷入无关内容。遵守提交署名约定，使用简短归档提交说明。
  验证通过后推送 `v2`，核实提交已在远端；推送失败则报告并保留状态，不强推。
  这是合并后的文档收尾，不能借此把未审查业务代码推到 `v2`。

## 4. 关闭已解决的 Issue

仅当原 Issue 的范围已全部兑现、没有后续能力/PR/未完成验收时，才简短注明 PR 与合并结果并
手动关闭（合并到 `v2` 不会自动处理默认分支的关闭关键字）。尚有范围或判断不明时保持打开。
已关闭则不重复发评论；多行评论用 `--body-file` 或结构化参数，不添加机器人触发提及。

## 5. 清理本变更的 git 资源

归档成功后，只处理第 1 步核实的本变更资源。目标 worktree 若有 staged、unstaged、未跟踪文件
或仍有活动会话，保留并报告；不以 `--force` 删除。只删除 head 对应的开发分支，不碰 `main`/`v2`。
先确认本地开发分支是 `v2` 的祖先、远端开发分支仍指向已合并 head；有新提交则保留。

```bash
git worktree remove <本变更worktree的真实路径>
git branch -d <本变更开发分支>
git push <已核实head远端> --delete <本变更开发分支>
```

每步失败就保留相关资源并报告，不换 `-D` 或强制删除。外部 fork 不擅自删除远端分支。
资源已不存在则记为已清理；不要为一个不存在的目录全局 prune 其他会话的 worktree。
用 worktree 列表、本地分支列表与 `git ls-remote --heads` 精确核对，网络失败不算“已无残留”。

## 6. 报告

返回 PR 链接、合并提交、归档提交与推送结果、Issue 状态、已清理/保留的资源和剩余人工验收。
任何步骤受阻都报告实际完成范围，不把部分收尾称为全部完成。
