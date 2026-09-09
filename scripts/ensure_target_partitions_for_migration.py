"""迁移前置步骤：在自建库上把源库（RDS）已有数据覆盖到的分区先建出来。

`alembic upgrade head` 只建出分区表的**结构**（父表本身），月度/站点子分区是导入时
按需建的（`ensure_fact_partitions`）——全新自建库没有任何历史导入，自然一个子分区
都没有。`pg_restore --data-only` 不会建分区，数据落不到不存在的子分区，逐个分区
报错（`relation "analysis.fact_daily_2026_09_us" does not exist`），这是本地小规模
演练（tasks 4.3）实际跑出来的错误，不是猜测——设计阶段漏考虑了这一步。

**必须在 app 容器内运行**：既要用 psycopg2 连源库（与宿主机同样可达），又要用项目
自己的 `ensure_fact_partitions` 连目标库——后者只在 Docker 内部网络可达，宿主机连不到
`db`。

**目标库连接串固定拼 compose 内部网络的 `db` 服务，不读 `config.settings.DATABASE_URL`
/ `database.SessionFactory`。** 迁移这一步发生在 design.md「Migration Plan」步骤 5
（切换 `.env` 的 `DATABASE_URL`）之前——此刻 `.env` 里的 `DATABASE_URL` 仍指向 RDS 源库，
而 `docker compose run --rm app` 经 `env_file: .env` 继承同一份配置。若这里复用全局
`SessionFactory`，`ensure_fact_partitions` 会在源库（RDS）上重复建一遍分区（RDS 早就有，
等于白跑），自建目标库依旧一个叶子分区都没有，紧接着的 `pg_restore --data-only` 会对
每一行事实数据报 `relation "analysis.fact_daily_2026_09_us" does not exist`
（Codex 审核 PR #84 第三轮）。

用法（由 migrate_to_selfhosted_pg.py 自动调用，一般不需要手动跑）::

    docker compose run --rm app python scripts/ensure_target_partitions_for_migration.py \\
        --source-url "postgresql://user:pass@rds-host/amazon_search" \\
        --db-name amazon_search
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.table.analysis.partitioning import PARTITIONED_TABLES, ensure_fact_partitions
from config import settings

# 一级分区表（fact_dropped/fact_growth）不按站点物理隔离，但 `ensure_fact_partitions`
# 的签名仍要求一个站点码做校验——随便取 ENABLED_SITES 的第一个即可，不影响建出来的分区
# （site_subpartition=False 时函数内部本就不会用它建站点子分区）。
_FALLBACK_SITE = settings.ENABLED_SITES.split(",")[0]


def _default_target_url(db_name: str) -> URL:
    """按 compose 内部网络拼目标库 DSN，与 `.env` 当前 `DATABASE_URL` 指向谁无关。

    `db` 是 compose 里的服务名，只在容器内部网络可达；`POSTGRES_PASSWORD` 经
    `env_file: .env` 同样注入到本容器（.env.example 顶层就有这一项，供 `db` 服务的
    `environment:` 与本脚本共用）。

    **用 `URL.create` 而不是拼 f-string。** 生产 `POSTGRES_PASSWORD` 若含 `@`/`/`/`#`/
    `%`/`:` 等 DSN 保留字符，直接塞进字符串会被解析成错误的 host/database，甚至让
    整个 URL 不可解析；这一步发生在源库已被 `_quiesce_source` 切成只读之后，DSN 解析
    失败会让迁移卡在"RDS 只读、自建库还没有数据"的半途（Codex 审核 PR #84 第四轮）。
    `create_engine` 接受 `URL` 对象，不需要再转回字符串。
    """
    password = os.environ["POSTGRES_PASSWORD"]
    return URL.create("postgresql", username="postgres", password=password, host="db", database=db_name)


def _source_leaf_partitions(source_url: str, table: str, has_site: bool) -> set[tuple[str, str]]:
    """返回源库里这张表**已建出的**叶子分区对应的 (site, 月份) 集合。

    直接读分区目录（`pg_partition_tree`），不从行数据反推月份——`fact_dropped`/
    `fact_growth` 的叶子分区在 `import_report` 里先建、后计算行，即便某一期结果为空
    也照常提交，源库因此可能存在"分区已建、一行没有"的叶子。旧写法按
    `SELECT DISTINCT site, date_trunc('month', ...) FROM analysis.{table}` 反推月份，
    看不到这种空叶子；`pg_dump --data-only` 仍会为它发一条 COPY 归档项，目标库没建
    这个分区，`pg_restore` 就会在 RDS 已切只读之后才报
    `relation "analysis.fact_dropped_2026_09" does not exist`
    （Codex 审核 PR #84 第七轮）。改读目录后，"分区存在但为空" 与 "分区不存在" 不再
    被行扫描混为一谈；副作用是不必再全表扫描取值，只查目录，比原来更快。
    """
    with psycopg2.connect(source_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_partition_tree(%s::regclass) t "
            "JOIN pg_class c ON c.oid = t.relid WHERE t.isleaf",
            (f"analysis.{table}",),
        )
        leaf_names = [row[0] for row in cur.fetchall()]

    result: set[tuple[str, str]] = set()
    for name in leaf_names:
        # 命名约定见 partitioning.py 的 month_partition_name/site_partition_name：
        # 月度叶子 `{table}_YYYY_MM`，两级方案下站点叶子在其后再拼 `_{site 小写}`。
        suffix = name[len(table) + 1 :]
        if has_site:
            year_month, _, site_lower = suffix.rpartition("_")
            site = site_lower.upper()
        else:
            year_month, site = suffix, _FALLBACK_SITE
        year, month = year_month.split("_")
        result.add((site, f"{year}-{month}-01"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    # 默认值直接读 os.environ，不走 scripts/_selfhosted_pg_env.default_db_name()（后者
    # 读宿主机的 .env 文件）——本脚本必须在 app 容器内跑（见模块 docstring），容器里没有
    # .env 文件，POSTGRES_DB 是 compose `env_file: .env` 直接注入的环境变量，跟
    # POSTGRES_PASSWORD（第 65 行同样读 os.environ）是同一个道理。用错哪一种都会在
    # 操作者改过 POSTGRES_DB 后悄悄回退到 "amazon_search"（Codex 审核 PR #84 第五轮
    # 修完 backup/migrate/verify 三个脚本后，排查同类写法时顺带发现的第四处）。
    parser.add_argument(
        "--db-name", default=os.environ.get("POSTGRES_DB", "amazon_search"),
        help="自建库里的数据库名，默认跟随注入到本容器的 POSTGRES_DB",
    )
    parser.add_argument(
        "--target-url", default=None,
        help="自建库 DSN，默认按 --db-name 与 POSTGRES_PASSWORD 拼 compose 内部网络地址",
    )
    args = parser.parse_args()

    target_url = args.target_url or _default_target_url(args.db_name)
    target_engine = create_engine(target_url)
    TargetSession = sessionmaker(bind=target_engine)

    from datetime import date as _date

    total = 0
    with TargetSession() as session:
        for table, scheme in PARTITIONED_TABLES.items():
            needed = _source_leaf_partitions(args.source_url, table, scheme.site_subpartition)
            print(f"{table}: 需要 {len(needed)} 个 (site, 月份) 分区")
            for site, month_str in needed:
                ensure_fact_partitions(session, table, site, _date.fromisoformat(month_str))
                total += 1
        session.commit()

    print(f"\n共确保 {total} 个分区（幂等，已存在的不会重建）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
