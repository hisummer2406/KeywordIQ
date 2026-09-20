"""自动/手动备份编排：复用 `scripts/backup_selfhosted_pg.py`，不重写备份逻辑
（`ops-backup` spec）。

`executor.backup_now()` 把整个脚本当一次子进程调用，只拿到 stdout/stderr——**不从
脚本内部改动获取结构化结果**，而是解析脚本本就打印的、按阶段顺序输出的进度行来
推断产物与失败阶段。这样不需要为了一个"顺便报个结果"的需求去碰一份已经稳定运行的
共享脚本；代价是脚本的打印文案一旦改动，这里的正则要跟着改——测试用脚本当前的
真实文案样例覆盖，脚本文案变了测试会先红，不会是静默失配。
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from ops_bot import crud, executor
from ops_bot.database import OpsAsyncSessionFactory
from scripts.backup_selfhosted_pg import _OSS_BACKUP_PREFIX

_SUCCESS_RE = re.compile(r"备份完成：(?P<key>\S+)")
_UPLOAD_LINE_RE = re.compile(r"上传到 OSS: [^/]+/(?P<key>\S+)（(?P<size_mb>[\d.]+) MB）")


def _parse_result(output: str) -> tuple[str | None, int | None, str | None]:
    """返回 `(object_key, size_bytes, failed_stage)`；成功时 `failed_stage` 为 `None`。"""
    success = _SUCCESS_RE.search(output)
    if success:
        object_key = success.group("key")
        upload = _UPLOAD_LINE_RE.search(output)
        size_bytes = int(float(upload.group("size_mb")) * 1024 * 1024) if upload else None
        return object_key, size_bytes, None

    if "上传到 OSS:" in output:
        return None, None, "UPLOAD"
    if "把 dump 从容器复制到本地临时文件" in output:
        return None, None, "COPY_OUT"
    return None, None, "DUMP"


async def run_backup(
    *, retention_days: int, task_id: str | None = None, actor_open_id: str | None = None
) -> str:
    """执行一次全量备份并记录结果，成功时顺带清理超期旧产物
    （design.md D3："每次备份成功后顺带清理"）。失败时抛异常——交给调度循环统一
    记为任务失败，这里只负责"把这次备份的结果如实落一行 `ops_backup_record`"。

    `executor.backup_now()` 是同步阻塞调用（`subprocess.run` 直接等子进程退出），
    ops-bot 是单进程单事件循环（`etc/keywordiq-ops-bot.service` 未指定多 worker），
    直接 await 会让备份跑的这几分钟里 `/ops/health`、飞书回调、其余调度任务全部
    停摆——与业务 app 把导入丢进线程池是同一条纪律，这里用 `asyncio.to_thread`。

    `actor_open_id` 留空表示定时触发（没有人工发起者）；备份是本变更唯一不需要
    确认卡片的受限动作，因此审计记录里 `confirmed_at` 恒为 `None`——它不是"忘了填"，
    是这个动作本就没有"确认"这一步（design.md D5 只要求重启/发布走确认流程）。
    """
    result = await asyncio.to_thread(executor.backup_now)
    object_key, size_bytes, failed_stage = _parse_result(result.output)
    status = "SUCCEEDED" if result.success else "FAILED"

    async with OpsAsyncSessionFactory() as session:
        await crud.record_backup(
            session, status=status, object_key=object_key, size_bytes=size_bytes, failed_stage=failed_stage
        )
        await crud.write_audit_log(
            session,
            task_id=task_id,
            actor_open_id=actor_open_id,
            action="BACKUP_NOW",
            target="scripts/backup_selfhosted_pg.py",
            target_sha=None,
            confirmed_at=None,
            result=status,
            failed_stage=failed_stage,
        )
        await session.commit()

    if not result.success:
        raise RuntimeError(f"备份失败（阶段：{failed_stage}）：{result.output[-500:]}")

    cleanup_summary = await cleanup_old_backups(retention_days=retention_days)
    return f"备份成功：{object_key}；{cleanup_summary}"


async def cleanup_old_backups(*, retention_days: int) -> str:
    """清理超过保留期的旧备份产物，返回清理摘要（写进触发本次清理的任务输出，
    供人工核对"清理了哪些"——design.md D3 要求清理动作可追溯）。
    """
    return await asyncio.to_thread(_cleanup_old_backups_sync, retention_days)


def _cleanup_old_backups_sync(retention_days: int) -> str:
    """`oss2` 是同步 SDK（阻塞网络调用），走线程池——理由同 `run_backup`。"""
    import oss2

    from config import settings

    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET)

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted: list[str] = []
    for obj in oss2.ObjectIterator(bucket, prefix=f"{_OSS_BACKUP_PREFIX}/"):
        last_modified = datetime.fromtimestamp(obj.last_modified, tz=UTC)
        if last_modified < cutoff:
            bucket.delete_object(obj.key)
            deleted.append(obj.key)

    return f"清理了 {len(deleted)} 个超过 {retention_days} 天的旧备份：{deleted}" if deleted else "无需清理"
