#!/usr/bin/env python3
"""提交门禁：git commit 之前按改动范围跑对应的验证。

挂在 ``PreToolUse`` 上、用 ``if: "Bash(git commit*)"`` 缩到提交这一步。
选提交而不是收尾（Stop）是刻意的：agent 改到一半停下来问问题是高频且正常的节奏，
卡在收尾会把「我想确认下方向」变成「先把这坨代码改绿再说」。而提交是一个明确的
「我认为这份改动可以了」的声明，在这里要求它是绿的没有歧义。

**本文件不含任何规则。** 它只做三件事：算出改了什么、决定跑哪几条命令、把失败原样
回喂。规则住在 ruff（pyproject.toml）和 test/test_conventions.py 里，那里同时被 CI
消费——检查器只有一份，本地和 CI 不会漂移。
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 后端改动的判定路径。alembic/ 也算：迁移写错会让整个部署起不来。
BACKEND_PREFIXES = ("app/", "scripts/", "test/", "alembic/")
BACKEND_TOP_LEVEL = ("config.py", "database.py", "main.py", "monitoring.py", "pyproject.toml")
FRONTEND_PREFIX = "frontend/"
# 浏览器插件：独立技术栈（esbuild + vitest），不接入 frontend/ 的 pnpm 工作区，
# 判据与 frontend/ 同构——改动范围含这个前缀就跑（issue #56 P3a）。
EXTENSION_PREFIX = "extension/"

# 本机测试库在 pgbase 容器，映射到 5433；5432 被别的项目的 PostgreSQL 占着。端口写错时探测连不上、
# 集成测试整片静默跳过（曾跳过 378 个而门禁照样放行），所以端口必须显式写出，不能靠默认 5432。
TEST_DB_URL = "postgresql://postgres:pwd123@localhost:5433/keywordiq_test"

# 单条检查的超时上限。后端全量约 5 秒、前端约 15 秒，给到分钟级只为兜住"卡死"这一种
# 情况——正常慢一点不该被误杀，真卡住则必须有个头。
CHECK_TIMEOUT = 300


def run(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = CHECK_TIMEOUT) -> tuple[int, str]:
    """跑一条检查，返回 (退出码, 合并输出)。

    **必须有超时。** 卡住的检查（pytest 抢咨询锁、半开连接的 psycopg2 连接）会让
    提交无限期挂起且没有任何输出——表现是"agent 不动了"而不是"检查失败了"，
    比失败难诊断得多。超时按失败处理：卡住本身就说明这次提交不该过。
    """
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            env={**os.environ, **(env or {})},
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part
            for part in (exc.stdout, exc.stderr) if part
        )
        return 124, f"超时（{timeout}s）未结束，已终止。\n{partial}".strip()
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_checks(
    checks: list[tuple[str, list[str], Path, dict]],
) -> list[tuple[str, list[str], str]]:
    """并发跑全部检查，返回失败项 ``(名称, 命令, 输出末尾)``，**顺序与传入顺序一致**。

    **并发不是为了省 CPU，是为了省等待。** 这些检查互不依赖：ruff/eslint/tsc 只读源码，
    pytest 与 vitest 跑在各自的进程里。串行时墙钟是各条之和（前端约 36 秒 = eslint 2.9
    + tsc 5.4 + vitest 28），并发后是最慢那条（约 28 秒）；前后端都改时省得更多——
    后端那 6 秒完全被前端的 28 秒盖住。

    **返回顺序必须稳定。** 按完成先后排的话，同一份改动两次提交会得到不同顺序的报告，
    读的人无从判断"这次和上次是不是同一批失败"。所以按传入顺序重排。

    并发度就是检查条数（最多 5 条），不设上限：它们本来就各自吃满多核（vitest 按核心数
    起 worker、pytest 也有并行），再叠一层限流只会让最慢那条更慢，而墙钟由它决定。
    """
    if not checks:
        return []

    results: dict[int, tuple[int, str]] = {}
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {
            pool.submit(run, cmd, cwd, env): index
            for index, (_, cmd, cwd, env) in enumerate(checks)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    failures = []
    for index, (name, cmd, _, _) in enumerate(checks):
        code, out = results[index]
        if code != 0:
            failures.append((name, cmd, "\n".join(out.splitlines()[-40:])))
    return failures


def staged_files(repo: Path, command: str) -> list[str]:
    """本次提交会带进去的文件。

    ``--cached`` 只看得见暂存区，而**有两种提交形态提的是工作树内容**，它们的改动
    在暂存区里可以完全不存在：

    - ``git commit -a``：提交时把已跟踪文件的改动一并暂存
    - ``git commit -- <路径>``：提交这些路径的工作树内容，绕过索引

    漏掉任何一种，``files`` 就是空的 → 一条检查都不跑 → **门禁静默放行**，而它长得
    和"检查全过了"一模一样。路径式提交这条真发生过（PR #66 的文案修复那次）：本仓库
    为并行会话推荐的正是按路径提交，也就是说推荐路径与门禁覆盖当时是错开的。
    ``divergent_files`` 早就为这两种形态开了豁免（它们不存在索引/工作树不一致的问题），
    却没人把同一件事告诉这里——豁免和识别是同一个事实的两半。

    **不按 pathspec 裁剪要跑哪些检查**，路径式提交一律连同全部未暂存改动一起算。
    裁剪要解析出 ``--`` 之后的路径，而解析出错的方向是**漏查**（提交信息里出现一个
    独立的 ``--`` 就够了，heredoc 写的多行信息尤其容易），代价是门禁形同虚设；
    不裁剪的代价只是多跑几十秒检查。这与 ``target_repo`` 的"宁可多查一遍也不要静默
    不查"是同一个取舍。
    """
    _, out = run(["git", "diff", "--cached", "--name-only"], repo)
    files = [line for line in out.splitlines() if line]

    if _commits_all(command) or _has_pathspec(command):
        _, unstaged = run(["git", "diff", "--name-only"], repo)
        files.extend(line for line in unstaged.splitlines() if line)

    return sorted(set(files))


def target_repo(command: str, cwd: Path) -> Path:
    """提交实际落地的仓库。**不能只信 ``cwd``。**

    钩子拿到的 ``cwd`` 是 Bash 会话的工作目录，而命令常常先切到别处再提交：

        cd /path/to/repo && git add -A && git commit -m "..."
        git -C /path/to/repo commit -m "..."

    基于 worktree 的会话天天是这个形态（会话开在 worktree 里、往主仓提交）。若照
    ``cwd`` 去查暂存区，那里空空如也，``files`` 为空 → 直接放行——**门禁被完整绕过，
    且悄无声息**。本仓库真发生过：一个未用 import 就是这样进的主干。

    故按 ``git -C <path>`` → 开头的 ``cd <path>`` → ``cwd`` 的顺序取。取到的路径不存在
    就退回 ``cwd``，宁可多查一遍也不要静默不查。
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # 引号不配对等无法解析的命令：退回 cwd 而不是抛异常。门禁自己崩掉会连
        # "该拦的没拦"都看不出来，比多查一遍糟得多。
        return cwd

    for i, token in enumerate(tokens):
        if token == "-C" and i + 1 < len(tokens):
            candidate = Path(tokens[i + 1]).expanduser()
            if candidate.is_dir():
                return candidate
        if token == "cd" and i + 1 < len(tokens):
            candidate = Path(tokens[i + 1]).expanduser()
            if candidate.is_dir():
                return candidate

    return cwd


def divergent_files(repo: Path, command: str) -> list[str]:
    """索引与工作树不一致、且会被本次提交带走的文件。

    **门禁验证的是工作树，裸 `git commit` 提交的是索引。** 两者对同一个文件不一致时，
    刚跑绿的检查并不描述这次提交的内容——检查没报错，只是查错了东西。

    真实发生过：``test/_gate_probe.py`` 被暂存后从磁盘删掉（索引状态 AD），一次裸
    ``git commit`` 把它提进了主干。它含一个未用 import，ruff 若看得见必报 F401，
    但磁盘上没那个文件，全程无人发现。

    两种提交形态天然没有这个问题，故排除：

    - ``git commit -a``：提交时把工作树一并暂存，提交的就是被 lint 的那份
    - ``git commit -- <路径>``：提交的是这些路径的**工作树**内容，同样是被 lint 的那份
    """
    if _commits_all(command) or _has_pathspec(command):
        return []

    _, staged = run(["git", "diff", "--cached", "--name-only"], repo)
    _, unstaged = run(["git", "diff", "--name-only"], repo)

    staged_set = {line for line in staged.splitlines() if line}
    unstaged_set = {line for line in unstaged.splitlines() if line}
    return sorted(staged_set & unstaged_set)


def _has_pathspec(command: str) -> bool:
    """命令里带没带 ``--`` 路径分隔符。"""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return "--" in tokens


def _commits_all(command: str) -> bool:
    """命令里带没带 -a/--all（含 ``-am "msg"`` 这种合并短选项）。"""
    for token in command.split():
        if token == "--all":
            return True
        if token.startswith("-") and not token.startswith("--") and "a" in token[1:]:
            return True
    return False


def _port_open(host: str = "localhost", port: int = 5432) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _test_db_reachable() -> bool:
    """能不能真的连上 keywordiq_test 并跑一条语句。

    端口通只说明"本机有个 PostgreSQL"，不说明"有这个库"。装了别的项目的 PG 时
    端口一样是通的，此时把 TEST_DATABASE_URL 喂下去，集成测试会因为连不上库而
    整片失败——一堆与本次改动毫无关系的红，比直接跳过糟得多。
    """
    try:
        import psycopg2
    except ImportError:
        return False

    try:
        conn = psycopg2.connect(TEST_DB_URL, connect_timeout=2)
    except Exception:
        return False
    conn.close()
    return True


def postgres_up() -> bool:
    """本地测试库在不在。在就解开集成测试的 skip gate，不在就只跑单元部分。

    连不上不算失败：CI 有真库兜底，本地没起库时把集成测试跳过、让门禁仍然可用，
    好过把门禁变成「必须先起 docker」。

    先探端口再连库：端口不通时省掉一次两秒的连接超时，这是最常见的情况
    （本机根本没起 PG）。
    """
    return _port_open() and _test_db_reachable()


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # 读不懂就别挡路

    command = payload.get("tool_input", {}).get("command", "")
    cwd = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", "."))

    # 兜底：settings.json 的 `if` 已经缩过范围，这里再确认一次，避免配置改动后误伤。
    if "git commit" not in command:
        sys.exit(0)

    # 提交可能落在别的目录（worktree 会话里 `cd 主仓 && git commit` 是常态）
    repo = target_repo(command, cwd)

    # 索引与工作树分歧时，下面跑的检查描述的不是这次提交的内容。先拦掉，
    # 否则得到的是一份"跑绿了但查错了东西"的验证——比不跑更有害。
    divergent = divergent_files(repo, command)
    if divergent:
        deny(
            "提交被门禁拦下——**暂存区与工作树不一致**，下面这些文件提交进去的内容"
            "与磁盘上的不是同一份：\n\n"
            + "\n".join(f"  - {f}" for f in divergent)
            + "\n\n门禁的 ruff / pytest 读的是磁盘（工作树），而裸 `git commit` 提交的是"
            "暂存区。两者不一致时，检查跑绿了也不代表这次提交是绿的——历史上一个含未用"
            "import 的文件正是这样进的主干（它被暂存后从磁盘删掉，ruff 根本看不见它）。\n\n"
            "选一种改法：\n"
            "  - 就要提交磁盘上这份 → `git add <路径>` 重新暂存后再提交\n"
            "  - 只想提交其中一部分 → `git commit -- <路径>`（按路径提交工作树内容）\n"
            "  - 那些是别的会话暂存的、与你无关 → 用 `git commit -- <你自己的路径>`，"
            "不要动它们的暂存区"
        )

    files = staged_files(repo, command)
    if not files:
        sys.exit(0)  # 没东西可验（如空 amend）

    backend = [
        f for f in files
        if f.startswith(BACKEND_PREFIXES) or f in BACKEND_TOP_LEVEL
    ]
    frontend = [f for f in files if f.startswith(FRONTEND_PREFIX)]
    extension = [f for f in files if f.startswith(EXTENSION_PREFIX)]

    if not backend and not frontend and not extension:
        sys.exit(0)  # 只改了文档 / openspec，放行

    checks: list[tuple[str, list[str], Path, dict]] = []

    if backend:
        checks.append(("ruff", ["uv", "run", "ruff", "check", "."], repo, {}))
        env = {"TEST_DATABASE_URL": TEST_DB_URL} if postgres_up() else {}
        checks.append(("pytest", ["uv", "run", "pytest", "test/", "-q"], repo, env))

    if frontend:
        fe = repo / "frontend"
        checks.append(("eslint", ["pnpm", "lint"], fe, {}))
        checks.append(("tsc", ["pnpm", "tsc"], fe, {}))
        checks.append(("vitest", ["pnpm", "test"], fe, {}))

    if extension:
        ext = repo / "extension"
        # 没有单独的 lint 步骤——tsc --noEmit（strict）+ vitest 是当前唯一的门禁，
        # 这是一个占位插件的刻意从简（design.md），不是漏配
        checks.append(("ext-typecheck", ["pnpm", "run", "typecheck"], ext, {}))
        checks.append(("ext-test", ["pnpm", "run", "test"], ext, {}))

    failures = [f"### {name} 失败（{' '.join(cmd)}）\n{tail}" for name, cmd, tail in run_checks(checks)]

    if failures:
        deny(
            "提交被门禁拦下——下面的检查没通过。请修好再提交，"
            "不要用 --no-verify 之类的方式绕过（这个门禁不走 git hook，绕不掉，"
            "但也请不要改 .claude/settings.json 关掉它来「解决」问题）。\n\n"
            + "\n\n".join(failures)
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
