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
import socket
import subprocess
import sys
from pathlib import Path

# 后端改动的判定路径。alembic/ 也算：迁移写错会让整个部署起不来。
BACKEND_PREFIXES = ("app/", "scripts/", "test/", "alembic/")
BACKEND_TOP_LEVEL = ("config.py", "database.py", "main.py", "monitoring.py", "pyproject.toml")
FRONTEND_PREFIX = "frontend/"

TEST_DB_URL = "postgresql://postgres:pwd123@localhost/keywordiq_test"


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def staged_files(repo: Path, command: str) -> list[str]:
    """本次提交会带进去的文件。

    ``git commit -a`` 会在提交时把已跟踪文件的改动一并暂存，此时 ``--cached``
    看不到它们——漏掉就等于门禁形同虚设，故一并取未暂存的已跟踪改动。
    """
    _, out = run(["git", "diff", "--cached", "--name-only"], repo)
    files = [line for line in out.splitlines() if line]

    if _commits_all(command):
        _, unstaged = run(["git", "diff", "--name-only"], repo)
        files.extend(line for line in unstaged.splitlines() if line)

    return sorted(set(files))


def _commits_all(command: str) -> bool:
    """命令里带没带 -a/--all（含 ``-am "msg"`` 这种合并短选项）。"""
    for token in command.split():
        if token == "--all":
            return True
        if token.startswith("-") and not token.startswith("--") and "a" in token[1:]:
            return True
    return False


def postgres_up(host: str = "localhost", port: int = 5432) -> bool:
    """本地测试库在不在。在就解开集成测试的 skip gate，不在就只跑单元部分。

    连不上不算失败：CI 有真库兜底，本地没起库时把 45 个集成测试跳过、
    让门禁仍然可用，好过把门禁变成「必须先起 docker」。
    """
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


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
    repo = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", "."))

    # 兜底：settings.json 的 `if` 已经缩过范围，这里再确认一次，避免配置改动后误伤。
    if "git commit" not in command:
        sys.exit(0)

    files = staged_files(repo, command)
    if not files:
        sys.exit(0)  # 没东西可验（如空 amend）

    backend = [
        f for f in files
        if f.startswith(BACKEND_PREFIXES) or f in BACKEND_TOP_LEVEL
    ]
    frontend = [f for f in files if f.startswith(FRONTEND_PREFIX)]

    if not backend and not frontend:
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

    failures: list[str] = []
    for name, cmd, cwd, env in checks:
        code, out = run(cmd, cwd, env)
        if code != 0:
            tail = "\n".join(out.splitlines()[-40:])
            failures.append(f"### {name} 失败（{' '.join(cmd)}）\n{tail}")

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
