"""按「近 7 天没出现过才算新词」重算已导入日报的 ``is_new``。

新口径只对之后导入的期次生效（``importer.load_fact`` 在写入时算定）；库里已有的日报仍是
旧口径（只看前一天）。不回填的话，同一份数据里新旧期次的新词数量不可比。

这是对「事实表只追加」的有意例外，与同期重复导入同类：只改 ``is_new`` 一列，且仅更新
值真的变化的行（``IS DISTINCT FROM``），不白写元组。

**回看窗口不完整的期次会被跳过**：最早一个已导入日期之后不足 7 天的锚定日，其窗口
左端已超出库内数据（保留清理或根本没导入），重算只会把所有词都判成新词，比旧口径更错。

用法::

    uv run python scripts/backfill_is_new.py --dry-run          # 真跑再回滚，报每期变化行数
    uv run python scripts/backfill_is_new.py --yes              # 全部启用站点
    uv run python scripts/backfill_is_new.py --site US --from 2026-07-01 --yes

口径只有一处定义（``importer.NEW_WORD_LOOKBACK_DAYS``），这里读同一个常量。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("backfill_is_new")


def split_anchors(
    available: list[date],
    lookback_days: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> tuple[list[date], list[date]]:
    """把已导入日期切成 (可回填, 回看窗口超出库内最早数据) 两组，均升序。"""
    if not available:
        return [], []
    earliest = min(available)
    ready: list[date] = []
    skipped: list[date] = []
    for anchor in sorted(available):
        if since and anchor < since:
            continue
        if until and anchor > until:
            continue
        (ready if anchor - timedelta(days=lookback_days) >= earliest else skipped).append(anchor)
    return ready, skipped


def _available_dates(session, site: str) -> list[date]:
    from sqlalchemy import text

    return list(session.execute(
        text("SELECT report_date FROM analysis.site_data_stats "
             " WHERE site = :s AND period_type = 'DAY' ORDER BY report_date"),
        {"s": site},
    ).scalars().all())


def _backfill_site(session, site: str, anchors: list[date], lookback_days: int, *, dry_run: bool) -> int:
    from sqlalchemy import text

    total = 0
    for anchor in anchors:
        result = session.execute(
            text(
                """
                UPDATE analysis.fact_daily f
                   SET is_new = NOT EXISTS (
                        SELECT 1 FROM analysis.fact_daily h
                         WHERE h.site = f.site AND h.keyword_id = f.keyword_id
                           AND h.report_date >= :new_from AND h.report_date < :rdate)
                 WHERE f.site = :site AND f.report_date = :rdate
                   AND f.is_new IS DISTINCT FROM NOT EXISTS (
                        SELECT 1 FROM analysis.fact_daily h
                         WHERE h.site = f.site AND h.keyword_id = f.keyword_id
                           AND h.report_date >= :new_from AND h.report_date < :rdate)
                """
            ),
            {"site": site, "rdate": anchor, "new_from": anchor - timedelta(days=lookback_days)},
        )
        changed = result.rowcount
        total += changed
        if dry_run:
            session.rollback()
        else:
            session.commit()
        logger.info("%s %s: 变更 %d 行%s", site, anchor, changed, "（dry-run，已回滚）" if dry_run else "")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=None, help="站点码，可重复。缺省为全部启用站点")
    parser.add_argument("--from", dest="since", type=date.fromisoformat, default=None, help="起始日期（含）")
    parser.add_argument("--to", dest="until", type=date.fromisoformat, default=None, help="结束日期（含）")
    parser.add_argument("--dry-run", action="store_true", help="真跑一遍再回滚，不改库")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.site_registry import is_known_site, normalize_site_code
    from app.table.upload.importer import NEW_WORD_LOOKBACK_DAYS
    from config import settings
    from database import SessionFactory, engine

    engine.echo = False

    requested = args.site or settings.enabled_site_codes
    unknown = [s for s in requested if not is_known_site(s)]
    if unknown:
        logger.error("站点码未登记: %s（英国是 GB 不是 UK）", ", ".join(unknown))
        return 2
    sites = [normalize_site_code(s) for s in requested]

    plan: dict[str, list[date]] = {}
    with SessionFactory() as session:
        for site in sites:
            ready, skipped = split_anchors(
                _available_dates(session, site), NEW_WORD_LOOKBACK_DAYS,
                since=args.since, until=args.until,
            )
            plan[site] = ready
            logger.info("%s: 可回填 %d 期，回看窗口不完整跳过 %d 期", site, len(ready), len(skipped))

    periods = sum(len(v) for v in plan.values())
    if periods == 0:
        logger.info("没有可回填的期次，结束")
        return 0

    if not args.dry_run and not args.yes:
        if input(f"确认重算 {periods} 期的 is_new？[y/N] ").strip().lower() != "y":
            logger.info("已取消")
            return 1

    changed = 0
    with SessionFactory() as session:
        for site, anchors in plan.items():
            changed += _backfill_site(session, site, anchors, NEW_WORD_LOOKBACK_DAYS, dry_run=args.dry_run)

    logger.info("完成：%d 期，变更 %d 行%s", periods, changed, "（dry-run，未改库）" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
