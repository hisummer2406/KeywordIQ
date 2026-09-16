"""迁移漂移巡检：生产库的 `alembic current` 是否等于代码的 `alembic heads`。

用 `docker compose run --rm app alembic ...` 而不是直接在 ops-bot 自己的环境跑
`alembic`——迁移要以业务 `app` 镜像里固定下来的那份代码与依赖为准，ops-bot 的
`uv` 环境版本可能与之不同步（两者是独立部署的两份环境）。
"""

from __future__ import annotations

import re
import subprocess

_REV_RE = re.compile(r"\b[0-9a-f]{12}\b")
_TIMEOUT_SECONDS = 60


def _extract_revision(output: str) -> str | None:
    match = _REV_RE.search(output)
    return match.group(0) if match else None


def _run(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["docker", "compose", "run", "--rm", "app", "alembic", *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _extract_revision(proc.stdout)


def is_in_sync() -> bool | None:
    """`None` 表示探测本身失败（容器起不来、超时等）——调用方不能把它当"同步"处理，
    否则一次探测失败会被误报成"迁移正常"，掩盖真正的漂移。
    """
    current = _run(["current"])
    heads = _run(["heads"])
    if current is None or heads is None:
        return None
    return current == heads
