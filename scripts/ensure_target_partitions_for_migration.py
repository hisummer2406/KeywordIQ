"""迁移前置步骤：在自建库上把源库（RDS）已有数据覆盖到的分区先建出来。

`alembic upgrade head` 只建出分区表的**结构**（父表本身），月度/站点子分区是导入时
按需建的（`ensure_fact_partitions`）——全新自建库没有任何历史导入，自然一个子分区
都没有。`pg_restore --data-only` 不会建分区，数据落不到不存在的子分区，逐个分区
报错（`relation "analysis.fact_daily_2026_09_us" does not exist`），这是本地小规模
演练（tasks 4.3）实际跑出来的错误，不是猜测——设计阶段漏考虑了这一步。

**必须在 app 容器内运行**：既要用 psycopg2 连源库（与宿主机同样可达），又要用项目
自己的 `SessionFactory`/`ensure_fact_partitions` 连目标库——后者只在 Docker 内部
网络可达，宿主机连不到 `db`。

用法（由 migrate_to_selfhosted_pg.py 自动调用，一般不需要手动跑）::

    docker compose run --rm app python scripts/ensure_target_partitions_for_migration.py \\
        --source-url "postgresql://user:pass@rds-host/amazon_search"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.table.analysis.partitioning import PARTITIONED_TABLES, ensure_fact_partitions
from config import settings
from database import SessionFactory

# 一级分区表（fact_dropped/fact_growth）不按站点物理隔离，但 `ensure_fact_partitions`
# 的签名仍要求一个站点码做校验——随便取 ENABLED_SITES 的第一个即可，不影响建出来的分区
# （site_subpartition=False 时函数内部本就不会用它建站点子分区）。
_FALLBACK_SITE = settings.ENABLED_SITES.split(",")[0]


def _months_needed(source_url: str, table: str, date_column: str, has_site: bool) -> set[tuple[str, str]]:
    """返回源库里这张表实际用到的 (site, 月份) 组合去重后的集合。

    按月而非按天聚合——分区是按月建的，逐天调用 `ensure_fact_partitions` 一样能跑通
    （幂等），只是白白多发很多次 DDL 语句。
    """
    site_expr = "site" if has_site else f"'{_FALLBACK_SITE}'"
    sql = f"SELECT DISTINCT {site_expr}, date_trunc('month', {date_column})::date FROM analysis.{table}"
    with psycopg2.connect(source_url) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return {(row[0], row[1].isoformat()) for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()

    from datetime import date as _date

    total = 0
    with SessionFactory() as session:
        for table, scheme in PARTITIONED_TABLES.items():
            needed = _months_needed(
                args.source_url, table, scheme.date_column, scheme.site_subpartition
            )
            print(f"{table}: 需要 {len(needed)} 个 (site, 月份) 分区")
            for site, month_str in needed:
                ensure_fact_partitions(session, table, site, _date.fromisoformat(month_str))
                total += 1
        session.commit()

    print(f"\n共确保 {total} 个分区（幂等，已存在的不会重建）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
