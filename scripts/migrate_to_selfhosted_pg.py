"""把数据从阿里云 RDS 迁移到自建 PostgreSQL 容器（`db` 服务）。

前提（不在本脚本里检查，迁移前必须先手动确认）：

1. 自建 `db` 容器已跑过 `docker compose exec app alembic upgrade head`，schema
   （含分区表、`category_stats`/`brand_stats` 等）已建好且核对过与 RDS 一致
   （见 openspec/changes/dockerize-selfhosted-infra/design.md 决策 4）
2. 选在低峰时段执行——本脚本会在确认无活跃导入后把源库切为只读（`_quiesce_source`），
   堵住检查通过到 dump 开始之间的写入窗口，但改不了已经发生的事：切只读之前若源库上
   还有别的写入在跑，那部分数据依然进不了 dump（见 design.md 风险「迁移窗口数据
   不一致」）

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
import re
import subprocess
import sys

_DUMP_PATH_IN_CONTAINER = "/tmp/keywordiq_migration_dump.custom"

# 匹配 postgres(ql)://user:pass@host 形态里的密码段，仅用于打印时脱敏——不用于连接。
_DSN_PASSWORD_RE = re.compile(r"(postgres(?:ql)?://[^:@/\s]+:)[^@\s]*(@)")

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


def _quiesce_source(source_url: str) -> None:
    """把源库切成拒绝新写事务，堵住"检查通过"与"pg_dump 开始"之间的写入窗口。

    `_check_no_active_import` 只查了一次 `import_batch`，既不覆盖用户操作、词库、竞对
    轮询等其余写路径，也挡不住检查通过后的下一刻才开始的新事务——这两者都会在这段窗口
    内静默丢入 RDS、却进不了 dump。`default_transaction_read_only` 对新会话立即生效，
    配合 `pg_terminate_backend` 收尾当前已连接、可能还留有未提交写事务的旧会话，
    使这段窗口内任何写入要么发生不了、要么在提交前就被切断连接回滚。
    这也提前落成了 design.md Migration Plan 步骤 6 承诺的终态——观察窗口期间
    RDS 本就该是只读的；这里只是把它提前到 dump 之前生效。

    若迁移中途放弃、需要继续用 RDS 服务，用 `PGOPTIONS` 带着覆盖值连接，跑
    `ALTER DATABASE <db> SET default_transaction_read_only = off;` 改回去——不能省
    `PGOPTIONS`：新连上的会话本身已经继承了库级只读默认值，直接跑这条 `ALTER DATABASE`
    会被这个只读挡下来（`ALTER DATABASE` 是对系统目录的写操作）；把 `SET
    default_transaction_read_only = off` 和 `ALTER DATABASE ...` 分两条语句依次执行
    同样能绕开，但若把两条语句写进同一次调用（同一个 `psql -c "stmt1; stmt2"`），
    Postgres 的简单查询协议会把它们隐式包进同一个事务，`SET` 生效前 `ALTER DATABASE`
    所在的事务早已在只读状态下开始——本地演练把这两种失败形态都跑出来过，不是猜测。
    """
    import psycopg2

    with psycopg2.connect(source_url) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            (db_name,) = cur.fetchone()
            cur.execute(
                f'ALTER DATABASE "{db_name}" SET default_transaction_read_only = on'
            )
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
    print(
        f"已将源库 {db_name} 切为只读并断开其余会话；若需放弃迁移继续用 RDS 服务，"
        f"跑（PGOPTIONS 不能省，见函数说明）：\n"
        f"  PGOPTIONS='-c default_transaction_read_only=off' psql \"<source_url>\" -c "
        f"'ALTER DATABASE \"{db_name}\" SET default_transaction_read_only = off'"
    )


def _redact(arg: str) -> str:
    return _DSN_PASSWORD_RE.sub(r"\1***\2", arg)


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print(f"$ {' '.join(_redact(c) for c in cmd)}")
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
        print("\n把源库切为只读，堵住检查通过之后到 dump 开始之间的写入窗口...")
        _quiesce_source(args.source_url)
    else:
        print("[--dry-run] 跳过 RDS 侧导入状态检查与只读切换")

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
    # 显式传 --db-name：此刻 .env 的 DATABASE_URL 仍指向 RDS 源库（第 5 步才切换），
    # 该脚本因此不复用 .env / SessionFactory，而是按这个库名拼 compose 内部网络的
    # db 服务地址，确保分区建在自建目标库上而不是重复建在源库上（Codex 审核 PR #84）。
    _run(
        [
            "docker", "compose", "run", "--rm", "app",
            "python", "scripts/ensure_target_partitions_for_migration.py",
            "--source-url", args.source_url,
            "--db-name", args.db_name,
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
