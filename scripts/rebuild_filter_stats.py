"""按已有切片回填筛选下拉统计（`category_stats` / `brand_stats`）。

迁移 `f5a6b7c8d9e0` 把这两张表从"按 (站点, 周期) 聚合的物化视图"改成"按
(站点, 周期, 日期) 分片的普通表"。旧视图是窗口级聚合，**无法拆成按日期的分片**——跨日期
去重之后，每个日期各自贡献了多少已经不可还原。所以新表建成空的，已有数据需要回填一次。

不回填的表现是：过滤区的类目下拉是空的，直到那个切片被重新导入为止。空下拉在界面上
看不出是"没数据"还是"还没算"，所以升级后应当尽快跑一次。

**切片清单来自 `site_data_stats`** ——它记录了每个 (站点, 周期, 日期) 的行数，正是
"库里有哪些切片"的权威答案，不需要在事实表上做 DISTINCT 扫描。

用法::

    # 先看会回填哪些切片（不改库）
    uv run python scripts/rebuild_filter_stats.py --dry-run

    # 全量回填
    uv run python scripts/rebuild_filter_stats.py --yes

    # 只处理某个站点，或只补尚未算过的切片（增量推进）
    uv run python scripts/rebuild_filter_stats.py --site US --missing-only --yes

每个切片一个事务，可随时 Ctrl-C 中断；`--missing-only` 让重跑从断点继续。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.table.analysis.filter_stats import rebuild_filter_stats
from config import settings
from database import SessionFactory

logger = logging.getLogger("rebuild_filter_stats")

SCHEMA = settings.DATABASE_SCHEMA


def _slices(session, *, site: str | None, missing_only: bool) -> list[tuple[str, str, object]]:
    """待回填的切片清单，按日期倒序（新的先补，用户最常看最近的数据）。"""
    sql = (
        f"SELECT s.site, s.period_type, s.report_date FROM {SCHEMA}.site_data_stats s "
        f"WHERE (:site IS NULL OR s.site = :site) "
    )
    if missing_only:
        # 已经算过的切片跳过。判据是库里的实际状态而不是脚本记的进度——进度会与真实状态
        # 漂移（跑一半崩了、或某个切片刚好被重新导入过），而"这个切片有没有统计行"不会。
        sql += (
            f"AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.category_stats c "
            f"WHERE c.site = s.site AND c.period_type = s.period_type "
            f"AND c.report_date = s.report_date) "
        )
    sql += "ORDER BY s.report_date DESC, s.site"
    return [tuple(r) for r in session.execute(text(sql), {"site": site}).all()]


def main() -> int:
    parser = argparse.ArgumentParser(description="按已有切片回填筛选下拉统计")
    parser.add_argument("--site", help="只处理该站点")
    parser.add_argument("--missing-only", action="store_true", help="只补尚未算过的切片")
    parser.add_argument("--dry-run", action="store_true", help="只列出待回填切片，不改库")
    parser.add_argument("--yes", action="store_true", help="确认执行")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.dry_run and not args.yes:
        parser.error("执行需要 --yes；建议先 --dry-run 核对")

    with SessionFactory() as session:
        pending = _slices(session, site=args.site, missing_only=args.missing_only)

        if not pending:
            logger.info("没有待回填的切片")
            return 0

        logger.info("待回填 %d 个切片", len(pending))
        if args.dry_run:
            for site, period, report_date in pending:
                logger.info("  %s %s %s", site, period, report_date)
            logger.info("(--dry-run，未改动任何数据)")
            return 0

        ok = failed = 0
        for site, period, report_date in pending:
            try:
                # 一个切片一个事务：中断时已完成的部分留在库里，重跑用 --missing-only 续。
                rebuild_filter_stats(
                    session, site=site, period=period, report_date=report_date, schema=SCHEMA
                )
                session.commit()
                ok += 1
                logger.info("  %s %s %s 完成", site, period, report_date)
            except Exception:
                session.rollback()
                logger.exception("  %s %s %s 失败", site, period, report_date)
                failed += 1

    logger.info("完成 %d，失败 %d", ok, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
