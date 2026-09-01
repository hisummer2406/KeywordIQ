# AGENTS.md

**本仓库的开发约定全部写在 [`CLAUDE.md`](./CLAUDE.md) 里，那才是你要读的文件。**
它不是 Claude 专用的，名字只是历史沿革——约 1000 行，涵盖数据模型、各功能域的约定、
以及每条决策「不这么做会怎样」。这里的陷阱绝大多数**错了不会报错**：测试全绿、日志干净、
界面看起来正常，只有数据是错的。动手前先读它。

本文件**刻意只写差异**：`CLAUDE.md` 里有少数几句是对着 Claude Code 这个运行环境写的，
在别的智能体里是**假的**。除下面这几条外，`CLAUDE.md` 的每一句都照单适用。

不复制它的内容是有意的。两份副本必然分叉，而这正是 `CLAUDE.md` 反复警告的那类缺陷
（曾有一张 `sites` 参考表与 `site_registry.py` 存同样的字段，从建表到删除没有任何代码写过
它，真正的害处是给站点元数据开了第二个事实来源）。

---

## 1. 提交门禁不保护你，得自己跑

`CLAUDE.md` 的「开发 harness（提交门禁）」一节说 `git commit` 之前会自动跑验证、不通过就
拒绝提交。**那道门禁挂在 Claude Code 的 `PreToolUse` hook 上**
（`.claude/settings.json` → `.claude/hooks/pre_commit.py`），其他智能体既不受它约束，
**也不会被它保护**——这是唯一一处失效方向不安全的差异：你会以为自己被检查过了。

提交前按改动范围自己跑：

| 改了什么 | 跑什么 |
|---|---|
| `app/` `scripts/` `test/` `alembic/` 或顶层 `.py` | `uv run ruff check . && uv run pytest test/ -q` |
| `frontend/` | `cd frontend && pnpm lint && pnpm tsc && pnpm test` |
| 只有文档 / `openspec/` | 不用跑 |

那一节里的其余内容（跑什么、为什么、基线棘轮怎么用）对你同样成立，照读。

## 2. 项目 skill 是普通 markdown，直接读

`CLAUDE.md` 末尾列的三个 skill（`start-change` / `local-db` / `verify-import`）是 Claude Code
的 skill 机制。你没有那个机制，但**文件本身就是 markdown**，按需直接读：

```
.claude/skills/start-change/SKILL.md    # 开一个变更：认领 → 建 worktree → 收工
.claude/skills/local-db/SKILL.md        # 起测试库、跑含集成测试的全量回归
.claude/skills/verify-import/SKILL.md   # 用真实体量 CSV 跑端到端导入并逐项断言
```

`start-change` 讲的东西 `CLAUDE.md` 的「并行会话」一节已经覆盖了要点，但**动手写代码前
读一遍那个文件**——这个仓库经常同时有多个会话在干活，默认假设不是「只有我一个人」。

`openspec` 是 CLI（`openspec list` / `status` / `validate` / `archive`），不是 skill，你能直接用。
`.claude/skills/openspec-*` 那几个只是把 CLI 的用法包了一层，读不读都行。

## 3. `AskUserQuestion` 就是「问用户」

`CLAUDE.md` 的「流程重量」一节提到 `AskUserQuestion` 工具和「一个调用最多带 4 个问题」。
那是 Claude Code 的工具名。对你而言这条约定的实质是：**澄清问题一次问完，不要串行问四轮**
——每轮都是一次完整的人机往返。

---

## 维护

`CLAUDE.md` 是唯一事实来源。**新增约定写进 `CLAUDE.md`，不要写进这里**；
只有当某条约定确实只对某一种运行环境成立时，才在本文件加一条差异说明。
