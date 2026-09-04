---
name: close-change
description: PR 评审通过、要合并时用——合并、清理 worktree/分支、归档 openspec 变更、把没做完的人工验收项转移到验收清单 issue、判断并关闭触发本次变更的原始 issue、扫一遍还有没有别的残留。当用户说"复审通过，合并 PR""合并并清理""收尾"之类，或一个 openspec 变更的代码已经合进 v2 但 `openspec/changes/` 下还留着它时使用。
---

# 合一个变更的尾

`start-change` 管认领、建 worktree、开 PR；这个 skill 接着往下——从"评审通过"
到"这个变更在这个仓库里已经不留任何痕迹（除了它自己在主 spec 和 git log 里的
记录）"。中间那段 `start-change` 只写到"git 层面三样清理"，本会话里
`openspec archive` 这一步被漏做过两次——都是在开始下一个变更、`openspec list`
冒出一条早就合并完的旧变更时才发现。这个 skill 把那之后的步骤接上。

## 1. 合并

```bash
gh pr merge <PR号> --merge --delete-branch=false
```

**用 `--merge`，不用 squash 或 rebase。** squash 会重写提交，本地
`change/<id>` 分支的提交因此不再是 `v2` 的祖先，后面 `git branch -d` 的保护
会拒绝删除——这时人会改用 `-D`，而那道"没合并进去的分支删不掉"的保护就此
作废。`--delete-branch=false` 是因为远端分支的删除留给后面单独做（有时
GitHub 会在合并时自动删，有时不会，第 2 步会核对）。

合并完核对一下，别想当然：

```bash
gh pr view <PR号> --json state,mergedAt,mergeCommit -q \
  '"state=\(.state) merged=\(.mergedAt) commit=\(.mergeCommit.oid[0:7])"'
git rev-list --parents -n1 <合并提交> | wc -w   # 应该是 3（提交本身 + 两个父）
```

两个父确认了这是真的合并提交，`branch -d` 的保护才有意义。

## 2. git 层面清理三样

这部分是 `start-change` 4.3 已经写过的——切回 `v2` 拉最新、`worktree remove`、
`branch -d`、远端 `push --delete`，三样都核对到空。细节见那份文档，这里不重复。

**有时候前两样已经不用你做了。** 别的会话（本仓库里经常是 Codex）可能已经先
把 worktree 目录删了，这种情况下 `git worktree remove` 会报"不是一个工作树"，
用 `git worktree prune` 把陈旧登记清掉就好，不是错误。远端分支同理，GitHub 有
时会在合并时顺手删掉，`push --delete` 报"远端分支不存在"也是正常结果，不是
需要处理的失败。

## 3. 归档 openspec 变更

```bash
openspec archive <变更id> -y
```

**这一步最容易漏**——本会话漏过两次，都是因为合并完直接一头扎进下一个任务，
`openspec list` 冒出一条早就合并完的旧变更时才想起来。合并 PR 之后、去做别的
事之前，先把这一步走完。

`tasks.md` 里如果还有没勾的项（通常是"人工实测交给用户"那类自动化验证不了的），
先把已经确认做完的（提交门禁、CI）补勾上，见第 4 步处理没法自动完成的那些，再
归档——`openspec archive` 对未完成任务只会警告不会阻止，但让记录反映真实完成
度还是值得做的。

**archive 工具会做一次安全检查**：如果这次变更的 spec delta 里，某条
`MODIFIED Requirement` 一个旧场景标题都没保留（哪怕新写的场景在语义上完全
覆盖了旧的），它会判断成"可能悄悄丢了场景"而拒绝归档，报"current spec
contains scenario(s) not present in the modified block"。这不是误报要绕过的
限制——它在提醒你确认这次改动是不是真的要让某个场景消失。多数时候答案是
"没有要丢的，只是拆分/改写时换了标题"，这时候把其中至少一个新场景的标题改回
旧的就够了（内容不用改，标题对上即可）。这个坑在本会话里踩过一次，第二次
写 spec delta 时就直接照着这条教训、从一开始保留了旧标题。

归档完核对：

```bash
openspec list   # 应该不再出现这个变更
```

然后照常提交、推送——这也是文档改动，不需要开新 PR，直接进 `v2`。

## 4. 没法自动验证的项不能就地消失

`tasks.md` 里那些"人工实测交给用户"的条目，归档之后会被埋进
`openspec/changes/archive/`，事实上等于丢失——除非先把它们转移到一个活的地方。

这个仓库习惯把这类验收项收进一条专门的、长期开着的 issue（本会话里是 #72，
标题类似"上线前人工验收清单"）。找到这条 issue（`gh issue list` 搜关键词，
别硬编码具体编号——每个仓库/每个阶段这条 issue 可能是别的号），在里面加一节，
把这次变更没法自动化验证的项列进去，写清楚要点开哪个页面、看哪个具体行为。
`tasks.md` 里原来那一条改成指向这条 issue，不要留着一个孤零零的未勾选框——
读的人不知道"没做"和"转移到别处了"是两回事。

## 5. 触发这次变更的原始 issue，该关就关

多数变更是在回应某条已有 issue。变更合并之后，回去看那条 issue 是不是因为这次
合并**彻底**解决了——如果这条 issue 本身还分几半、这次只做完一半，先别关，等
全部做完再关（本会话的 issue #65 就是先做完独立站半留着开、Amazon 半合并后
才关）。

真要关的话，关闭评论里写清楚：做了什么、如果分了几半分别是哪个 PR 做的、关键
的取舍决定是什么（比如"近似值 vs 新建明细表"这种，其中的推理值得被检索到，
不该只留在 PR 描述里、下次没人会想到去翻旧 PR）。

```bash
gh issue comment <issue号> --body-file <文件>
gh issue close <issue号> --reason completed
```

## 6. 扫一遍还有没有别的残留

不只是这次变更自己的三样，做一次更宽的核对：

```bash
git worktree list
git branch --list 'change/*'
git ls-remote --heads origin | grep 'change/'
openspec list
gh pr list --state open --json number,title
git status --short --untracked-files=all   # 主仓工作树本身该是干净的
```

**别人的 worktree 不要碰**，尤其是其他 agent（本仓库常见的是 Codex）留下的
只读评审 checkout。它们经常会在评审对象合并之后由它自己清掉，但不总是立刻——
看到一个还在，只报告"这是谁的、对应哪个 PR、那个 PR 合没合"，不要因为看着像
残留就顺手删掉。除非用户明确说了要清理这一类，处理别人的工作区不是这个 skill
的范围。
