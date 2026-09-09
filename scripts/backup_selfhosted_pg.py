"""自建 PostgreSQL 的定时备份：`pg_dump` 全量 → 上传到阿里云 OSS。

替代阿里云 RDS 消失的自动备份能力（design.md 决策 6）。用 host cron 调用本脚本，
不新增常驻的备份容器——一条定时任务，复用项目已有的 OSS 凭证与 `docker compose`。

**备份是全量（schema + data），不是 `--data-only`。** 与迁移脚本
（`migrate_to_selfhosted_pg.py`）刻意不同：迁移的目标库已经由 `alembic upgrade head`
建过 schema，只需要搬数据；备份要能在“自建库整个没了”的最坏情况下独立重建，必须自带
schema，不能依赖恢复时还记得先跑一遍迁移。

**产物落在 db 容器内再复制出来，不直接挂宿主机目录写。** `docker compose exec` 里
`pg_dump` 的 stdout 重定向到宿主机文件在 `-T`（禁用 TTY）下是可行的，但用
`--file=` 让 pg_dump 自己在容器内写、再 `docker compose cp` 取出，能在复制失败时
明确区分"备份没做出来"与"做出来了但没搬出容器"两种状态，用 stdout 重定向两者会
糊在一起。

用法（供 cron 调用）::

    uv run python scripts/backup_selfhosted_pg.py [--compose-service db] [--keep-local]

退出码非 0 表示备份失败，cron 里应接告警（哪怕只是发到已有的钉钉/邮件渠道）。

恢复演练（tasks 5.3 已用真实 OSS 桶实测跑通，下面是验证过、可直接照抄的命令）::

    # 1. 从 OSS 下载备份文件（或用 docker compose cp 从别处复制进容器）
    docker compose cp <本地备份文件> db:/tmp/restore.custom

    # 2. 恢复。目标数据库须已存在（`db` 服务启动时由 POSTGRES_DB 自动建出），
    #    因此**不要加 --create**——那会让 pg_restore 尝试 DROP/CREATE 自己当前
    #    连接的数据库，报 "cannot drop the currently open database"（这条错误会被
    #    pg_restore 当作可忽略继续跑完，实测确认最终数据仍然正确，但命令本身是错的，
    #    生产恢复时不该让人对着一堆报错怀疑恢复有没有成功）。--clean --if-exists
    #    会先清空已有对象再按备份重建，同一条命令对"空库"与"库里已有旧数据"都适用。
    docker compose exec -T db pg_restore --clean --if-exists \\
        --username postgres --dbname amazon_search /tmp/restore.custom

    # 3. 核对：关键表行数是否符合预期（至少确认 permission/role 等种子表非空，
    #    证明 schema 与种子数据都恢复了，不只是恢复了个空壳）
    docker compose exec -T db psql -U postgres -d amazon_search \\
        -tAc "SELECT count(*) FROM analysis.permission"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from scripts._selfhosted_pg_env import default_db_name

_DUMP_PATH_IN_CONTAINER = "/tmp/keywordiq_backup.custom"
# 与导入用的 imports/ 前缀区分，避免两类对象在同一前缀下混淆（design.md 决策 6）。
_OSS_BACKUP_PREFIX = "backups/postgres"


def _run(cmd: list[str]) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _dump_full(compose_service: str, db_name: str) -> None:
    """全量 dump（schema + data），落在容器内。"""
    _run(
        [
            "docker", "compose", "exec", "-T", compose_service,
            "pg_dump", "-Fc", f"--file={_DUMP_PATH_IN_CONTAINER}",
            "--username", "postgres", "--dbname", db_name,
        ]
    )


def _copy_out(compose_service: str, local_path: Path) -> None:
    _run(
        [
            "docker", "compose", "cp",
            f"{compose_service}:{_DUMP_PATH_IN_CONTAINER}", str(local_path),
        ]
    )


def _cleanup_container(compose_service: str) -> None:
    _run(["docker", "compose", "exec", "-T", compose_service, "rm", "-f", _DUMP_PATH_IN_CONTAINER])


def _upload(local_path: Path, object_key: str) -> None:
    """复用项目已有的 OSS 凭证配置，不新增一套。

    用 `oss2.resumable_upload` 而不是 `bucket.put_object_from_file`：后者是单次 PutObject，
    OSS 单个 PutObject 请求的对象大小上限是 5 GiB——决策 5 里满窗数据约 50 GB，全量 dump
    早晚会超过这个上限，届时 `put_object_from_file` 直接报错、当天备份整个失败。
    `resumable_upload` 按文件大小自动在简单上传与分片上传之间切换，超过阈值即走分片
    （`MultipartUpload`），单个分片仍在 5 GiB 以内，不受这个上限约束（Codex 审核 PR #84）。
    """
    import oss2

    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET)
    oss2.resumable_upload(bucket, object_key, str(local_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-service", default="db")
    parser.add_argument("--db-name", default=default_db_name())
    parser.add_argument(
        "--keep-local", action="store_true", help="保留本地临时文件（默认用完即删）"
    )
    args = parser.parse_args()

    if not settings.OSS_ENABLED:
        print("错误：OSS_ENABLED=false，本脚本依赖 OSS 存放备份，先配好 OSS_* 再启用。")
        return 1

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    object_key = f"{_OSS_BACKUP_PREFIX}/{timestamp}.custom"

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{timestamp}.custom"

        print(f"\n在 {args.compose_service} 容器内执行 pg_dump（全量）...")
        _dump_full(args.compose_service, args.db_name)

        print(f"\n把 dump 从容器复制到本地临时文件 {local_path} ...")
        _copy_out(args.compose_service, local_path)

        print("\n清理容器内的临时文件...")
        _cleanup_container(args.compose_service)

        size_mb = local_path.stat().st_size / 1024 / 1024
        print(f"\n上传到 OSS: {settings.OSS_BUCKET}/{object_key}（{size_mb:.1f} MB）...")
        _upload(local_path, object_key)

        if args.keep_local:
            kept = Path.cwd() / local_path.name
            local_path.replace(kept)
            print(f"\n本地副本保留在 {kept}")

    print(f"\n备份完成：{object_key}")
    print(
        "保留策略建议直接用 OSS 生命周期规则管理该前缀的过期删除（阿里云控制台或 "
        "oss2 的 put_bucket_lifecycle），不在本脚本里重新实现——这与备份本身是否成功"
        "是两件独立的事，失败模式也不同（过期规则配错了了不起是备份留太久/太短，"
        "备份本身失败则是真的没有可恢复的数据）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
