"""不可变版本定位与迁移/回滚边界的辅助判断（design.md D5）。

只接受完整 40 位 commit SHA，且必须落在允许分支的历史上；发布计划里"是否包含
数据库迁移""是否是需要人工评估的回滚"两项判断也在这里，全部基于 `git` 子进程
（本模块运行在 host 级 ops-bot 进程里，`git` 命令与仓库工作树天然可达）。
"""

from __future__ import annotations

import re
import subprocess

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_TIMEOUT = 30


def is_valid_sha_format(sha: str) -> bool:
    return bool(_SHA_RE.fullmatch(sha))


def current_sha(ref: str = "HEAD") -> str | None:
    """当前检出的 commit SHA——发布确认卡片用它展示"当前版本"。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", ref], capture_output=True, text=True, timeout=10, check=True
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.strip() or None


def change_summary(from_sha: str, to_sha: str, *, max_lines: int = 10) -> list[str] | None:
    """`from_sha..to_sha` 之间的提交摘要（一行一条），供确认卡片展示"这次发布带来
    了什么"。超过 `max_lines` 截断，不代表发布范围本身被截断——只是卡片篇幅有限。
    """
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"{from_sha}..{to_sha}"],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"…等共 {len(lines)} 条提交"]
    return lines


def _is_ancestor(ancestor: str, descendant: str) -> bool | None:
    """`ancestor` 是否是 `descendant` 的祖先提交。

    `None` 表示探测本身失败（git 不可用、引用在本地对象库里找不到等）——调用方
    不能把它当"是"或"否"处理，那两种误判分别会放行一次不该放行的发布、或拒绝
    一次其实合法的操作。
    """
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def is_ancestor_of_branch(sha: str, branch: str = "v2") -> bool | None:
    """`sha` 是否在 `origin/<branch>` 的提交历史上——发布只接受这类 SHA。

    先 `git fetch` 确保本地看到的是最新的分支头，不然"分支推进后旧 SHA 判不出
    祖先关系"这种滞后会被误判成"不在允许分支上"。
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return _is_ancestor(sha, f"origin/{branch}")


def migrations_changed(sha: str, *, against: str = "HEAD") -> bool | None:
    """目标 `sha` 与 `against`（默认当前 HEAD）相比，`alembic/versions/` 目录内容
    是否不同——用作"这次发布是否包含数据库迁移"的判据。

    **不是精确的 alembic heads 对比。** 精确判断要在目标 SHA 的树上重建
    down_revision 图找出真正的 head，这里用"目录内容变了"当代理：偏保守，宁可把
    "只改了迁移文件里的注释"也算作"包含迁移"，不能漏判真正新增了迁移文件的情形。
    """
    try:
        current = subprocess.run(
            ["git", "rev-parse", f"{against}:alembic/versions"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        target = subprocess.run(
            ["git", "rev-parse", f"{sha}:alembic/versions"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return current != target


def rollback_needs_review(sha: str, *, current: str = "HEAD") -> bool | None:
    """目标 `sha` 是否是"回滚且带着比当前少的迁移"——命中时 MUST NOT 自动执行，
    需要人工评估（design.md D5「迁移与回滚边界」，不自动降级数据库）。

    判据：目标 SHA 是当前 HEAD 的祖先（在历史上更早）、且两者的迁移目录内容不同
    （意味着当前 HEAD 比目标多了迁移）。不是"目标不是祖先"就一定安全——同一条
    分支上目标更早但迁移集合没变化（比如只改了业务代码）时不算需要评估的回滚。
    """
    is_older = _is_ancestor(sha, current)
    if is_older is None:
        return None
    if not is_older:
        return False
    return migrations_changed(sha, against=current)
