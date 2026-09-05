"""把数据从阿里云 RDS 迁移到自建 PostgreSQL 容器（`db` 服务）。

前提（不在本脚本里检查，迁移前必须先手动确认）：

1. 自建 `db` 容器已跑过 `docker compose exec app alembic upgrade head`，schema
   （含分区表、`category_stats`/`brand_stats` 等）已建好且核对过与 RDS 一致
   （见 openspec/changes/dockerize-selfhosted-infra/design.md 决策 4）
2. 选在低峰时段执行——迁移窗口内 RDS 上不应有新写入，否则窗口期的写入会丢失
   （见 design.md 风险「迁移窗口数据不一致」）

**用 db 容器自带的 pg_dump/pg_restore，不要求宿主机安装 PostgreSQL 客户端**：
`postgres:15.x` 镜像本身就带这两个二进制，`docker compose exec db ...` 直接借用，
产物落在容器内的 /tmp，用完即删，不落宿主机磁盘。

用法::

    uv run python scripts/migrate_to_selfhosted_pg.py \\
        --source-url "postgresql://user:pass@rds-host/amazon_search" \\
        [--db-name amazon_search] [--jobs 4] [--dry-run]

跑完之后必须再跑 ``verify_selfhosted_migration.py`` 核对行数，核对通过才能切换
`.env` 的 `DATABASE_URL`/`DATABASE_URL_ASYNC`。**本脚本不自动做核对**——迁移与核对
分成两个独立步骤，是为了核对也能在切换 `.env` 之后的观察窗口内单独重跑（见
design.md「Migration Plan」）。

退出码非 0 表示迁移失败，不应继续后续步骤。
"""

from __future__ import annotations

import argparse
import subprocess
import sys

_DUMP_PATH_IN_CONTAINER = "/tmp/keywordiq_migration_dump.custom"

# 这几张表由 Alembic 迁移在建库时直接写入固定的种子数据（RBAC 权限矩阵、迁移版本号），
# 不是"导入进来的业务数据"。自建库跑过 `alembic upgrade head` 后已经有这些行，
# `pg_restore --data-only` 再原样搬一遍会主键冲突——本地演练（tasks 4.3）实际跑出来的
# 错误，不是猜测。源库与目标库的种子数据本就该一致（都来自同一套迁移脚本），排除掉
# 完全正确，不是"绕过冲突"式的权宜之计。
_MIGRATION_SEEDED_TABLES = ("permission", "role", "role_permission", "alembic_version")


def _check_no_active_import(source_url: str) -> None:
    """确认 RDS 侧没有正在跑的导入。有则中止——迁移期间不该有并发写入。"""
    import psycopg2

    with psycopg2.connect(source_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM analysis.import_batch WHERE status IN ('PROCESSING', 'QUEUED', 'UPLOADING')"
        )
        active = cur.fetchone()[0]
    if active:
        raise SystemExit(
            f"错误：RDS 侧还有 {active} 条批次处于 PROCESSING/QUEUED/UPLOADING，"
            "先等它们跑完（或失败）再迁移，否则迁移会漏掉这部分写入。"
        )


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print(f"$ {' '.join(cmd)}")
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True, help="RDS 的 DATABASE_URL（psycopg2 格式）")
    parser.add_argument("--db-name", default="amazon_search", help="自建库里的数据库名")
    parser.add_argument("--compose-service", default="db", help="自建库的 compose 服务名")
    parser.add_argument("--jobs", type=int, default=4, help="pg_restore 并行度")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的命令，不真的连库/写入"
    )
    args = parser.parse_args()

    if not args.dry_run:
        print("确认 RDS 侧没有正在进行的导入...")
        _check_no_active_import(args.source_url)
    else:
        print("[--dry-run] 跳过 RDS 侧导入状态检查")

    print(f"\n从 RDS 导出 analysis schema 的数据到容器内 {_DUMP_PATH_IN_CONTAINER} ...")
    dump_cmd = [
        "docker", "compose", "exec", "-T", args.compose_service,
        "pg_dump", "--data-only", "--schema=analysis", "-Fc",
        f"--file={_DUMP_PATH_IN_CONTAINER}",
    ]
    for table in _MIGRATION_SEEDED_TABLES:
        dump_cmd += ["--exclude-table", f"analysis.{table}"]
    dump_cmd += ["--dbname", args.source_url]
    _run(dump_cmd, dry_run=args.dry_run)

    print("\n在自建库上补齐源库已用到的分区（alembic upgrade head 只建父表，"
          "子分区按需建，全新库还没有任何历史导入）...")
    _run(
        [
            "docker", "compose", "run", "--rm", "app",
            "python", "scripts/ensure_target_partitions_for_migration.py",
            "--source-url", args.source_url,
        ],
        dry_run=args.dry_run,
    )

    print(f"\n恢复到自建库 {args.db_name}（并行度 {args.jobs}）...")
    _run(
        [
            "docker", "compose", "exec", "-T", args.compose_service,
            "pg_restore", "--data-only", f"-j{args.jobs}",
            "--disable-triggers",
            # 必须显式给 --username：不给的话 pg_restore 用 `docker compose exec`
            # 的默认执行用户（root）连本地 socket，而 Postgres 里没有 root 这个角色，
            # 表现是「role "root" does not exist」——与 --dbname 是否给完整 DSN 无关，
            # pg_dump 那条命令因为 --dbname 传的是完整 DSN（含用户名）而不受影响，
            # 这里 --dbname 只是库名，两条命令的"用户名从哪来"因此并不对称。
            "--username", "postgres",
            "--dbname", args.db_name,
            _DUMP_PATH_IN_CONTAINER,
        ],
        dry_run=args.dry_run,
    )

    print("\n清理容器内的临时 dump 文件...")
    _run(
        ["docker", "compose", "exec", "-T", args.compose_service, "rm", "-f", _DUMP_PATH_IN_CONTAINER],
        dry_run=args.dry_run,
    )

    print(
        "\n迁移命令已执行完毕。接下来必须跑："
        "\n  uv run python scripts/verify_selfhosted_migration.py --source-url <同上>"
        "\n核对通过后才能切换 .env 的 DATABASE_URL/DATABASE_URL_ASYNC。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
