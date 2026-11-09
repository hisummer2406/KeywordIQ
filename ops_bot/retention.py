"""运行业务镜像里的保留清理入口，并把结果解析成结构化数据。

走 `docker compose run --rm app` 而不是 import 业务代码：ops-bot 与 `app` 是独立部署的
两份环境（同 `checks/alembic_drift.py`），清理逻辑必须以 `app` 镜像里的版本为准。
"""

from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30 * 60
_STDERR_LOG_CHARS = 2000


class RetentionRunError(Exception):
    """清理入口没有给出可信结果。消息会进飞书告警，不放子进程输出。"""


def run_prune(*, dry_run: bool) -> dict:
    """阻塞运行一次清理，调用方必须放进线程池。"""
    mode_flag = "--dry-run" if dry_run else "--yes"
    command = [
        "docker", "compose", "run", "--rm", "app",
        "python", "scripts/prune_retention.py", "--json", mode_flag,
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RetentionRunError(f"运行超过 {_TIMEOUT_SECONDS // 60} 分钟未结束") from exc
    except OSError as exc:
        raise RetentionRunError("无法启动清理进程") from exc

    if proc.returncode != 0:
        logger.error("保留清理退出码 %s: %s", proc.returncode, proc.stderr[-_STDERR_LOG_CHARS:])
        raise RetentionRunError(f"清理进程退出码 {proc.returncode}")

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RetentionRunError("清理结果无法解析") from exc
    if not isinstance(result, dict) or result.get("status") not in ("ok", "lock_skipped"):
        raise RetentionRunError("清理结果无法解析")
    return result
