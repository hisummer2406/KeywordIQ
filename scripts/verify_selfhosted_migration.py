"""核对自建 PostgreSQL 与迁移源（阿里云 RDS）的关键表是否一致。

迁移路径见 ``migrate_to_selfhosted_pg.py``。这个脚本单独成文件，是因为核对不只在
迁移那一刻用一次——切换 `.env` 指向自建库之后、释放 RDS 之前的整个观察窗口内，
都可能需要重新核对（见 openspec/changes/dockerize-selfhosted-infra/design.md
「Migration Plan」第 4/6 步），但两次核对的判据不同，**不能共用同一条比较逻辑**。

核对的是**行数与最新期次**，不是逐行 diff——`fact_daily`/`fact_weekly` 满窗上亿行，
逐行比较代价过高；行数一致 + 最新 `report_date`/主键一致，足以发现"迁移中途中断、
少拷贝了几个分区"这类真实会发生的问题。

**两种用法，判据不同**：

- **迁移前的一次性门禁**（Migration Plan 第 4 步，切换 `.env` 之前）：默认模式，
  要求源库与目标库**严格相等**。此刻目标库还没开始服务，任何差异都意味着搬迁本身
  不完整。

      uv run python scripts/verify_selfhosted_migration.py --source-url <RDS DATABASE_URL>

- **观察窗口内的重新核对**（Migration Plan 第 6 步，切换 `.env` 之后、释放 RDS
  之前）：加 `--observation-window`。这个窗口里 RDS 已被
  `migrate_to_selfhosted_pg.py` 的 `_quiesce_source` 持久切为拒绝新写、不再变化，
  天然是一份冻结的迁移基准；而自建库在正常服务、行数与最新期次只会比基准更多、
  更新，不会再跟基准完全相等。若仍按严格相等比较，观察窗口内第一条真实的用户/
  竞对/导入写入就会让核对报"不一致"，而这正是观察窗口应该发生、也应该放行的事——
  把它跟"搬迁不完整"混为一谈会让这个工具在它自称支持的场景下反而没法用（Codex
  审核 PR #84 第二十三轮）。这个模式改成校验目标库**不低于**基准（行数
  `target ≥ source`，最新期次 `target ≥ source` 或源库当时本就为空），不要求相等。

      uv run python scripts/verify_selfhosted_migration.py --source-url <RDS DATABASE_URL> \\
          --observation-window

退出码非 0 表示有表核对不一致（或观察窗口模式下目标库反而落后于冻结的源库快照），
可直接用于迁移流程的门禁。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.table.analysis.filter_stats import _FILTER_STATS
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
    ("keyword_brand_hit", None),
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
    ("competitor_store_product_event", None),
    ("user_seller_group", None),
    ("user_seller_watch", None),
    ("user_store_watch", None),
    ("competitor_watch_preview", None),
    ("pricing_rule", None),
    ("pricing_sheet", None),
    ("pricing_sheet_version", None),
    ("pricing_selection", None),
    ("pricing_selection_log", None),
    *((table, None) for table, _, _ in _FILTER_STATS),
)
# 上面这一截（`category_stats`/`brand_stats`）不在 `migration_scope.V2_TABLES` 里
# ——它们没有 ORM 模型，全靠原生 SQL 写入（见 `app/table/analysis/filter_stats.py`
# 的 `rebuild_filter_stats`），因此不受 alembic autogenerate 管理。但它们仍是
# `analysis` schema 下的普通表（迁移 `f5a6b7c8d9e0` 已把它们从物化视图改成普通表），
# `pg_dump --data-only --schema=analysis` 按整个 schema 搬迁、不看 V2_TABLES，同样会
# 把它们搬过去；`analysis_crud.py` 靠它们提供类目/品牌下拉选项。核对清单若只从
# V2_TABLES 派生就会漏掉这两张——数据没搬完整时下拉会显示不全或缺项，核对却仍会全绿
# 放行切换（Codex 审核 PR #84 第七轮）。表名直接取自 `_FILTER_STATS`（该模块实际
# 写入的表），不再手写一份新清单，避免两处各自维护而漂移。


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


def _latest_not_behind(source_latest: str | None, target_latest: str | None) -> bool:
    """观察窗口模式下判断目标库的最新期次没有落后于冻结的源库快照。

    `report_date`/`anchor_date` 都是 ISO 格式日期字符串，按字典序比较即按时间序
    比较，与既有的相等比较用的是同一种字符串。源库当时为空（`None`）时目标库
    此后长出任何数据都不算落后；源库当时有数据而目标库现在反而是空的，才是真正
    的倒退（不应该发生——除非搬迁本身就不完整），必须拦住。
    """
    if source_latest is None:
        return True
    if target_latest is None:
        return False
    return target_latest >= source_latest


def _table_ok(
    s_count: int,
    s_latest: str | None,
    t_count: int,
    t_latest: str | None,
    *,
    observation_window: bool,
) -> bool:
    """单张表的核对判据，抽成纯函数方便直接单测（main() 只负责取数与打印）。"""
    if observation_window:
        return t_count >= s_count and _latest_not_behind(s_latest, t_latest)
    return s_count == t_count and s_latest == t_latest


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
    parser.add_argument(
        "--observation-window", action="store_true",
        help=(
            "切换 .env 后、释放 RDS 前的观察窗口内重新核对时加上此参数：源库（RDS）"
            "此时已被 migrate_to_selfhosted_pg.py 持久切为拒绝新写，是冻结的迁移"
            "基准；目标库（自建）在正常服务，行数与最新期次只会比基准更多/更新。"
            "默认（迁移前的一次性核对门禁）要求严格相等。"
        ),
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
        ok = _table_ok(
            s_count, s_latest, t_count, t_latest,
            observation_window=args.observation_window,
        )
        status = "OK" if ok else "MISMATCH"
        print(
            f"  [{status}] {table}: 源 count={s_count} latest={s_latest} | "
            f"目标 count={t_count} latest={t_latest}"
        )
        if not ok:
            mismatches.append(table)

    if mismatches:
        if args.observation_window:
            print(f"\n核对不通过：{mismatches} 落后于冻结的源库快照，需排查后再继续观察窗口。")
        else:
            print(f"\n核对不通过：{mismatches} 与源库不一致，不应切换 DATABASE_URL。")
        return 1

    verdict = "均不落后于迁移基准" if args.observation_window else "均一致"
    print(f"\n核对通过：关键表行数与最新期次{verdict}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
