"""把数据从阿里云 RDS 迁移到自建 PostgreSQL 容器（`db` 服务）。

前提（不在本脚本里检查，迁移前必须先手动确认）：

1. 自建 `db` 容器已建好 schema（含分区表、`category_stats`/`brand_stats` 等），且核对过
   与 RDS 一致（见 openspec/changes/dockerize-selfhosted-infra/design.md 决策 4）。

   **不能用 `docker compose exec app alembic upgrade head`。** 此刻切换还没发生，`.env`
   的 `DATABASE_URL`/`DATABASE_URL_ASYNC` 通常仍指向 RDS，而 `app` 容器的环境变量是同一份
   `.env` 经 `env_file:` 注入的——`exec` 进一个尚未按新形态起过的 `app` 容器会找不到容器,
   即便临时起一个也是升级 RDS 的 schema（RDS 本来就已是 head，实际什么也没发生），而不是
   在目标自建库上建表（Codex 审核 PR #84 第五轮）。改用 `docker compose run --rm` 起一个
   一次性容器，显式覆盖两个 DSN 指向 `db` 服务（内部网络地址，`POSTGRES_PASSWORD` 与
   `db` 服务共用同一份 `.env` 注入，见 `.env.example` 的密码字符集约束）：

   **`${POSTGRES_PASSWORD}`/`${POSTGRES_DB}` 必须在容器内展开，不能在宿主机 shell 展开。**
   `docker compose run -e VAR=value` 里的 `value` 是宿主机 shell 在调用 `docker compose`
   之前就展开的普通命令行参数——docker compose 只会把 `.env` 的键值对经 `env_file:`
   注入*容器*的环境，从不导出到调用它的宿主机 shell。若照字面把下面这条命令粘进
   宿主机终端，宿主机 shell 会把两个 `${...}` 展开成空字符串（除非操作者事先手动
   `export` 过），拼出 `postgresql://postgres:@db/`，本该初始化目标库的 alembic 命令
   因此连不上库，迁移在这一步就卡住（Codex 审核 PR #84 第八轮）。改用
   `app sh -c '...'`、并把整段命令用单引号包住：单引号让宿主机 shell 原样传递
   `${POSTGRES_PASSWORD}`/`${POSTGRES_DB}` 这两段文本，展开发生在容器内的 `sh` 里，
   读到的是 `app` 服务经 `env_file: .env` 已经注入好的那两个变量：

       docker compose run --rm app sh -c '
         DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@db/${POSTGRES_DB}" \
         DATABASE_URL_ASYNC="postgresql+asyncpg://postgres:${POSTGRES_PASSWORD}@db/${POSTGRES_DB}" \
         alembic upgrade head
       '
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

退出码非 0 表示迁移失败，不应继续后续步骤。**可以直接重跑本脚本**——恢复步骤前会先
清空目标库里即将被写入的表（`_reset_target_tables`），中途失败不会在重试时撞主键冲突
（Codex 审核 PR #84 第十四轮）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._selfhosted_pg_env import default_db_name

_DUMP_PATH_IN_CONTAINER = "/tmp/keywordiq_migration_dump.custom"

# 匹配 postgres(ql)://user:pass@host 形态里的密码段，仅用于打印时脱敏——不用于连接。
_DSN_PASSWORD_RE = re.compile(r"(postgres(?:ql)?://[^:@/\s]+:)[^@\s]*(@)")

# 这几张表由 Alembic 迁移在建库时直接写入固定的种子数据（RBAC 权限矩阵、迁移版本号），
# 不是"导入进来的业务数据"。自建库跑过 `alembic upgrade head` 后已经有这些行，
# `pg_restore --data-only` 再原样搬一遍会主键冲突——本地演练（tasks 4.3）实际跑出来的
# 错误，不是猜测。源库与目标库的种子数据本就该一致（都来自同一套迁移脚本），排除掉
# 完全正确，不是"绕过冲突"式的权宜之计。
_MIGRATION_SEEDED_TABLES = ("permission", "role", "role_permission", "alembic_version")


def _check_no_active_import(source_url: str, *, after_quiesce: bool = False) -> None:
    """确认源库没有正在跑的导入（PROCESSING/QUEUED/UPLOADING）。有则中止。

    调用两次，语义不同：

    **首次**（`_quiesce_source` 之前）拦的是"迁移前源库本就有导入在跑"——等它跑完
    或失败即可，不是本脚本造成的问题。

    **`after_quiesce=True`**（`_quiesce_source` 之后）拦的是本脚本自己制造的一种
    竞态：首次检查通过、`_quiesce_source` 切只读并 `pg_terminate_backend` 断开所有
    会话之间存在一个非零窗口，若恰好有个导入在这个窗口内开始，它的批次状态在
    `_execute_locked` 里先于事实写入单独提交为 `PROCESSING`
    （`app/table/upload/import_service.py`）；随后连接被切断，事实写入那段跟着回滚，
    但 `PROCESSING` 这次提交已经落地。该批次自己的失败处理原本想把状态改成
    `FAILED`，但此刻源库已被设成持久只读，这次写入同样失败——批次因此永久卡在
    `PROCESSING`，且会连同其余数据一起被 `pg_dump --data-only` 原样搬到目标库，
    没有任何环节会报错（Codex 审核 PR #84 第九轮）。这种情况下"等它跑完"不成立
    ——连接已断、源库已只读，它不会自己恢复，只能人工核实。
    """
    import psycopg2

    with psycopg2.connect(source_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM analysis.import_batch WHERE status IN ('PROCESSING', 'QUEUED', 'UPLOADING')"
        )
        active = cur.fetchone()[0]
    if not active:
        return
    if after_quiesce:
        raise SystemExit(
            f"错误：源库切为只读、断开旧会话之后，仍有 {active} 条批次处于 "
            "PROCESSING/QUEUED/UPLOADING——大概率是它恰好在「检查通过」到「断开连接」"
            "这段窗口内开始，被切断连接后卡在这个状态，且源库已只读，它的失败处理写不进"
            "新状态，不会自己恢复。不要让它原样进入目标库：先核实这些批次对应的数据是否"
            "完整（很可能表现为该期数据缺失或写了一半），必要时改回可写"
            "（见 `_quiesce_source` 说明的 `PGOPTIONS` 回退命令）手动把它们标记为 FAILED，"
            "确认干净后再重新执行本脚本。"
        )
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

    这个只读设置是持久的（`ALTER DATABASE ... SET`，不是会话级），`.env` 的
    `DATABASE_URL` 改回指向 RDS 不会自动清掉它——**迁移中途放弃**、或**迁移完成后在
    design.md Migration Plan 步骤 6 的观察窗口内决定回滚**，两种情况都需要主动解除，
    否则应用配置层面已经指回 RDS，实际连上去仍然只能读不能写（spec.md「迁移窗口内可
    回滚」两个场景都要求这一步，Codex 审核 PR #84 第四轮）。用 `PGOPTIONS` 带着覆盖值
    连接，跑 `ALTER DATABASE <db> SET default_transaction_read_only = off;` 改回去——
    不能省 `PGOPTIONS`：新连上的会话本身已经继承了库级只读默认值，直接跑这条
    `ALTER DATABASE` 会被这个只读挡下来（`ALTER DATABASE` 是对系统目录的写操作）；把
    `SET default_transaction_read_only = off` 和 `ALTER DATABASE ...` 分两条语句依次
    执行同样能绕开，但若把两条语句写进同一次调用（同一个 `psql -c "stmt1; stmt2"`），
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
        f"或迁移完成后在观察窗口内回滚到 RDS，都要跑（PGOPTIONS 不能省，见函数说明）：\n"
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


def _reset_target_tables(compose_service: str, db_name: str, *, dry_run: bool) -> None:
    """清空目标库里即将被 `pg_restore --data-only` 写入的表，让这一步对重跑安全。

    `pg_restore --data-only` 不是幂等操作——它对已有行执行 INSERT。若上一次因某个表
    或分区中途失败（网络抖动、磁盘满、并行恢复的某个 worker 报错）而退出，目标库处于
    "部分表已经有数据"的状态；此时按文档字面重跑本脚本，`pg_restore` 会在这些已恢复
    过的表上撞主键冲突，而不是把迁移续上——源库已经被 `_quiesce_source` 切成持久
    只读，操作者卡在一个"重试即报错、不重试又没有别的路"的状态（Codex 审核 PR #84
    第十四轮）。

    在 `pg_restore` 之前无条件 TRUNCATE 全部即将被恢复的表，把这一步变成可安全重复
    执行的操作：不管是首次运行还是失败后重试，起点都统一是"这些表在目标库里是空的"。
    TRUNCATE 一个分区父表会级联清空全部叶子分区，不需要单独枚举分区。种子表
    （`_MIGRATION_SEEDED_TABLES`）不在清空范围内——它们本就不参与 `pg_restore
    --data-only`（dump 阶段已用 `--exclude-table` 排除），TRUNCATE 掉会把
    RBAC 权限矩阵清空。
    """
    if dry_run:
        print("[--dry-run] 跳过清空目标表（不会真的连接自建库）")
        return
    exclude = ", ".join(f"'{t}'" for t in _MIGRATION_SEEDED_TABLES)
    list_tables_sql = (
        "SELECT tablename FROM pg_tables WHERE schemaname = 'analysis' "
        f"AND tablename NOT IN ({exclude})"
    )
    proc = subprocess.run(
        [
            "docker", "compose", "exec", "-T", compose_service,
            "psql", "-U", "postgres", "-d", db_name, "-tAc", list_tables_sql,
        ],
        capture_output=True, text=True, check=True,
    )
    tables = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not tables:
        return
    truncate_sql = "TRUNCATE " + ", ".join(f"analysis.{t}" for t in tables) + " CASCADE"
    subprocess.run(
        [
            "docker", "compose", "exec", "-T", compose_service,
            "psql", "-U", "postgres", "-d", db_name, "-c", truncate_sql,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True, help="RDS 的 DATABASE_URL（psycopg2 格式）")
    parser.add_argument(
        "--db-name", default=default_db_name(),
        help="自建库里的数据库名，默认跟 .env 的 POSTGRES_DB 走",
    )
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
        print("\n复查断开连接后有没有批次恰好卡在「检查通过」到「断开连接」的窗口里...")
        _check_no_active_import(args.source_url, after_quiesce=True)
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

    print(f"\n清空目标库 {args.db_name} 里即将被恢复的表（让这一步对重跑安全）...")
    _reset_target_tables(args.compose_service, args.db_name, dry_run=args.dry_run)

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
        f"\n  uv run python scripts/verify_selfhosted_migration.py --source-url <同上> "
        f"--db-name {args.db_name}"
        "\n（--db-name 与本次迁移用的库名保持一致，不要依赖核对脚本自己的默认值）"
        "\n核对通过后才能切换 .env 的 DATABASE_URL/DATABASE_URL_ASYNC。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
