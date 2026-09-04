---
name: close-change
description: PR 评审通过、要合并并收尾时用——合并到 v2、清理对应 worktree 与分支、归档 OpenSpec 变更。默认走一分钟内完成的快路径；只有确有未完成的人工验收项或原 Issue 已被完整解决时，才额外更新验收清单或关闭 Issue。当用户说"复审通过，合并 PR"、"合并并清理"、"收尾"，或一个 OpenSpec 变更的代码已经合进 v2 但 `openspec/changes/` 下仍留目录时使用。
---

# 合一个变更的尾

`start-change` 管认领、建 worktree、开 PR；本 skill 从评审通过走到变更归档。收尾的默认目标是让代码、分支和 OpenSpec 同步结束，**不是顺手完成所有项目管理写作**。人工验收转移、关闭原 Issue 都有价值，但只有实际触发条件成立时才做。

## 0. 先确认，不等待

主工作区必须干净：

```bash
git status --short --untracked-files=all
```

若有输出，先确认归属；不属于本次收尾的内容只报告，不能删除、暂存或移动。不要在混有它们的主工作区继续 `pull`、归档或提交。

用户已给出 PR 号时直接使用；否则才从变更 ID 查关联 PR。一次取回所需状态：

```bash
gh pr view <PR号> --json number,state,baseRefName,mergedAt,mergeCommit,reviewDecision,mergeStateStatus,url
gh pr checks <PR号>
```

- PR 的 base 不是 `v2`，或状态既非 `OPEN` 也非 `MERGED`：停止并核对。
- `OPEN` 的 PR 若评审或 CI 未通过：报告当前状态并结束本次运行，**不在 skill 内等待 CI**。
- `MERGED`：记录合并提交，跳过第 1 节。

## 1. 合并（仅 OPEN 且已绿）

```bash
gh pr merge <PR号> --merge --delete-branch=false
gh pr view <PR号> --json state,mergedAt,mergeCommit
```

使用 `--merge` 而非 squash/rebase：本地 `change/<id>` 分支仍是 v2 祖先，后续 `git branch -d` 才能作为保护闸。合并结果不是 `MERGED` 时停止，不要继续清理。

## 2. 同步 v2 并清理本变更的 git 资源

```bash
git switch v2
git pull --ff-only origin v2
git merge-base --is-ancestor <合并提交> HEAD

git worktree remove ~/.claude-worktrees/<变更id>
git branch -d "change/<变更id>"
git push origin --delete "change/<变更id>"
```

`merge-base` 无输出才表示本地已包含合并提交。若 worktree 或远端分支已被其他流程删除，分别用 `git worktree prune` 或记为已清理；不要把正常的“不存在”当失败。只处理本变更的资源，别人的 worktree 和分支一律不碰。

核对本变更已无残留：

```bash
git worktree list | grep '<变更id>'
git branch --list "change/<变更id>"
git ls-remote --heads origin "change/<变更id>"
```

三条均应无输出。

## 3. 归档并提交（默认必做）

```bash
openspec archive <变更id> -y
openspec validate --strict --all
git add openspec/
git commit -m "docs(openspec): 归档 <变更id>"
git push origin v2
```

归档前只补勾**已实际完成**的任务；未完成的人工验收项按第 4 节处理。`archive` 若提示某个旧场景标题没有被保留，先核对是否真的要删除该场景；只是改写时保留至少一个旧标题再重试。归档与验证通过后，显式提交并推送归档结果，不能只停在工作树里。

## 4. 仅在有未完成的人工验收项时转移

只有 `tasks.md` 仍有无法自动验证的人工项时才执行：找到现有的长期验收清单 Issue（`gh issue list` 搜关键词，不硬编码编号），以简短 checklist 追加“页面/账号类型/要观察的行为”，然后在 `tasks.md` 标明已转移并再执行第 3 节归档。

找不到合适的活跃验收 Issue 时，报告并请用户指定去处或确认新建；不能让未完成项随归档消失。没有这类任务时完全跳过本节。

## 5. 仅在原 Issue 被本次完整解决时关闭

若原 Issue 还包含后续能力、后续 PR 或未完成验收，保持打开。只有范围已完整兑现时才：

```bash
gh issue comment <Issue号> --body "已由 PR #<PR号> 实现并合并至 v2。"
gh issue close <Issue号> --reason completed
```

需要补充关键取舍时再扩写评论；不要为每次收尾生成长篇复述。用户未要求关闭且范围是否完成不明确时，报告判断，不擅自关闭。

## 6. 最后报告

报告 PR、归档提交、已清理的本变更资源，以及被转移或仍待人工验收的事项。不要为了“扫干净”再枚举或删除其他会话的 worktree、分支或未跟踪文件。
