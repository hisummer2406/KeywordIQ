"""核对自建 PostgreSQL 与迁移源（阿里云 RDS）的关键表是否一致。

迁移路径见 ``migrate_to_selfhosted_pg.py``。这个脚本单独成文件，是因为核对不只在
迁移那一刻用一次——切换 `.env` 指向自建库之后、释放 RDS 之前的整个观察窗口内，
都可能需要重新核对（见 openspec/changes/dockerize-selfhosted-infra/design.md
「Migration Plan」第 4/6 步）。

核对的是**行数与最新期次**，不是逐行 diff——`fact_daily`/`fact_weekly` 满窗上亿行，
逐行比较代价过高；行数一致 + 最新 `report_date`/主键一致，足以发现"迁移中途中断、
少拷贝了几个分区"这类真实会发生的问题。

用法::

    uv run python scripts/verify_selfhosted_migration.py --source-url <RDS DATABASE_URL>

退出码非 0 表示有表核对不一致，可直接用于迁移流程的门禁。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._selfhosted_pg_env import default_db_name

# (表名, 用于核对"最新一条"的列)。fact_growth/fact_dropped 用 anchor_date，
# 两个事实表天报/周报的分区列名不同（CLAUDE.md 已经踩过这个坑），故分开写。
#
# 覆盖 analysis schema 下**全部**持久存数据的表，只排除 `migrate_to_selfhosted_pg.py`
# 的 `_MIGRATION_SEEDED_TABLES`（`permission`/`role`/`role_permission`/`alembic_version`
# ——这四张由 Alembic 迁移在建库时写种子数据，不经 `pg_dump --data-only` 搬迁，源库与
# 目标库的种子行本就该分别由各自的迁移历史生成，核对它们的计数没有意义）。此前只列了
# 事实表与两张维度表共七张，`user_center`、词库、`import_batch`、全部 `competitor_*`
# 共享表与关注表都不在核对范围内——这些表若在 `pg_restore` 时因故少落了数据，核对仍会
# 全绿放行切换，实际后果是用户登不进去、收藏的词库/关注清单凭空消失（Codex 审核
# PR #84 第五轮）。除四张事实表外一律只核对行数（`latest_col=None`）：这些表没有像
# `report_date` 那样统一的"期次"列，行数不一致已经足以拦住"部分表没搬完整"这类真实
# 会发生的问题，不必为每张表各自找一个类比列。
_TABLES_WITH_LATEST_COL = (
    ("fact_daily", "report_date"),
    ("fact_weekly", "report_date_week"),
    ("fact_dropped", "anchor_date"),
    ("fact_growth", "anchor_date"),
    ("dim_keyword", None),
    ("dim_product", None),
    ("dim_brand", None),
    ("dim_category", None),
    ("site_data_stats", None),
    ("user_center", None),
    ("keyword_group", None),
    ("keyword_group_item", None),
    ("import_batch", None),
    ("competitor_seller", None),
    ("competitor_seller_asin", None),
    ("competitor_asin_report", None),
    ("competitor_store", None),
    ("competitor_store_product", None),
    ("competitor_store_price", None),
    ("competitor_store_rank", None),
    ("user_seller_group", None),
    ("user_seller_watch", None),
    ("user_store_watch", None),
    ("competitor_watch_preview", None),
)


def _source_counts(source_url: str) -> dict[str, tuple[int, str | None]]:
    """连源库（RDS 通常在同一 VPC，宿主机可直接访问）查行数与最新期次。"""
    results: dict[str, tuple[int, str | None]] = {}
    with psycopg2.connect(source_url) as conn, conn.cursor() as cur:
        for table, latest_col in _TABLES_WITH_LATEST_COL:
            cur.execute(f"SELECT count(*) FROM analysis.{table}")
            count = cur.fetchone()[0]
            latest = None
            if latest_col:
                cur.execute(f"SELECT max({latest_col}) FROM analysis.{table}")
                value = cur.fetchone()[0]
                latest = str(value) if value is not None else None
            results[table] = (count, latest)
    return results


def _target_counts(compose_service: str, db_name: str) -> dict[str, tuple[int, str | None]]:
    """自建 db 容器不对宿主机暴露端口，只能经 `docker compose exec` 进容器内查询。"""
    results: dict[str, tuple[int, str | None]] = {}
    for table, latest_col in _TABLES_WITH_LATEST_COL:
        count = _psql_scalar(compose_service, db_name, f"SELECT count(*) FROM analysis.{table}")
        latest = None
        if latest_col:
            # psql -tAc 对 NULL（含"没有行"时 MAX() 的结果）输出空字符串，不是字面
            # "NULL"——必须显式转回 Python None，否则和源库这边的 None 比较恒不等，
            # 表现成"表明明都是空的，核对却报不一致"（本地演练 tasks 4.3 实际踩到过）。
            raw = _psql_scalar(
                compose_service, db_name, f"SELECT max({latest_col}) FROM analysis.{table}"
            )
            latest = raw if raw else None
        results[table] = (int(count), latest)
    return results


def _psql_scalar(compose_service: str, db_name: str, sql: str) -> str:
    proc = subprocess.run(
        [
            "docker", "compose", "exec", "-T", compose_service,
            "psql", "-U", "postgres", "-d", db_name,
            "-tAc", sql,
        ],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True, help="迁移源（RDS）的 DATABASE_URL")
    parser.add_argument(
        "--compose-service", default="db", help="自建库的 compose 服务名（默认 db）"
    )
    parser.add_argument(
        "--db-name", default=default_db_name(),
        help="自建库里的数据库名，默认跟 .env 的 POSTGRES_DB 走",
    )
    args = parser.parse_args()

    print("核对源库（RDS）...")
    source = _source_counts(args.source_url)
    print("核对目标库（自建 PostgreSQL 容器）...")
    target = _target_counts(args.compose_service, args.db_name)

    mismatches = []
    for table, _ in _TABLES_WITH_LATEST_COL:
        s_count, s_latest = source[table]
        t_count, t_latest = target[table]
        ok = s_count == t_count and s_latest == t_latest
        status = "OK" if ok else "MISMATCH"
        print(
            f"  [{status}] {table}: 源 count={s_count} latest={s_latest} | "
            f"目标 count={t_count} latest={t_latest}"
        )
        if not ok:
            mismatches.append(table)

    if mismatches:
        print(f"\n核对不通过：{mismatches} 与源库不一致，不应切换 DATABASE_URL。")
        return 1

    print("\n核对通过：关键表行数与最新期次均一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
