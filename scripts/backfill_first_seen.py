"""存量关键词的首次入榜回填：用库内仍保留的事实数据，把能证明的最早日期补上。

**上线后跑一次**（迁移之后、或第一次导入之后均可）。迁移只加列，既有关键词全是 ``UNKNOWN``
且日期为空；不回填的话，它们会在各自下一次出现时被导入一个一个顺手补成 ``EARLIEST_OBSERVED``，
那是一次 UPDATE 风暴（每个被改的词都要在 ``keyword`` 的 GIN 索引上插新条目），所以集中做。

**只产出 ``EARLIEST_OBSERVED``，永不产出 ``CONFIRMED``。** 事实表只留最近三个月，库内最早的
一条记录不等于这个词的历史首次——更早的可能早被清理。把它写成「已确认」，上线日附近的词
就会被当成真的首次入榜，而没有任何报错。

口径只有一处：比较键（期次日期，日报优先于周报）与 ``importer._upsert_keywords`` 相同，
按期次**升序**逐期推进（升序时每个词只在第一次出现的那期被改一次，之后的期次因为更晚而
零更新；降序则每个更早的期次都会改写一遍）。可重复运行，第二次零更新。

用法::

    # 真跑一遍再回滚，数字是真的，库不变
    uv run python scripts/backfill_first_seen.py --dry-run

    # 全部启用站点，并抽 20 个词核对最早日期的事实行是否真的存在
    uv run python scripts/backfill_first_seen.py --yes --sample 20

脚本持有导入的咨询锁：回填与导入互斥，避免在 2C4G 上同时全库扫描抢 IO；拿不到锁（有导入在跑）
就退出，不排队。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("backfill_first_seen")

_TABLES = {"DAY": ("fact_daily", "report_date"), "WEEK": ("fact_weekly", "report_date_week")}
_RANK = {"DAY": 0, "WEEK": 1}


def list_periods(session, site: str) -> list[tuple[date, str]]:
    """站点库内仍有事实数据的 (期次日期, 周期)，按（日期升序，日报先于周报）。

    来源是 ``site_data_stats``——事实表上做 DISTINCT 要扫一遍几千万行。保留清理会同步清理
    它的残留行，所以这里列出的期次就是仍在库内的期次。
    """
    from sqlalchemy import text

    rows = session.execute(
        text("SELECT report_date, period_type FROM analysis.site_data_stats WHERE site = :s"),
        {"s": site},
    ).all()
    return sorted(((r[0], r[1]) for r in rows), key=lambda x: (x[0], _RANK[x[1]]))


def backfill_period(session, site: str, period: str, report_date: date) -> int:
    """把一期的事实数据当作证据推进首次记录，返回被修改的词数。不提交。"""
    from sqlalchemy import text

    table, date_col = _TABLES[period]
    result = session.execute(
        text(
            f"""
            UPDATE analysis.dim_keyword k
               SET first_seen_date = :rdate,
                   first_seen_period_type = :period,
                   first_seen_basis = CASE WHEN k.first_seen_basis = 'UNKNOWN'
                                           THEN 'EARLIEST_OBSERVED' ELSE k.first_seen_basis END
              FROM analysis.{table} f
             WHERE f.site = :site AND f.{date_col} = :rdate
               AND k.keyword_id = f.keyword_id AND k.site = :site
               AND (k.first_seen_date IS NULL
                    OR (CAST(:rdate AS date), CAST(:rank AS int))
                       < (k.first_seen_date,
                          CASE k.first_seen_period_type WHEN 'DAY' THEN 0 ELSE 1 END))
            """
        ),
        {"site": site, "period": period, "rdate": report_date, "rank": _RANK[period]},
    )
    return result.rowcount


def basis_counts(session, site: str) -> dict[str, int]:
    from sqlalchemy import text

    rows = session.execute(
        text("SELECT first_seen_basis, count(*) FROM analysis.dim_keyword "
             " WHERE site = :s GROUP BY 1"),
        {"s": site},
    ).all()
    return {r[0]: r[1] for r in rows}


def sample_check(session, site: str, n: int) -> tuple[int, int]:
    """随机抽 ``n`` 个 ``EARLIEST_OBSERVED`` 的词，核对它记录的最早日期在对应事实表里
    真的有这一行。返回 (抽到的数量, 核对不上的数量)——后者应为 0。"""
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT keyword_id, keyword, first_seen_date, first_seen_period_type "
            "  FROM analysis.dim_keyword "
            " WHERE site = :s AND first_seen_basis = 'EARLIEST_OBSERVED' "
            "   AND first_seen_date IS NOT NULL "
            " ORDER BY random() LIMIT :n"
        ),
        {"s": site, "n": n},
    ).all()
    bad = 0
    for keyword_id, keyword, fdate, ptype in rows:
        table, date_col = _TABLES[ptype]
        found = session.execute(
            text(f"SELECT 1 FROM analysis.{table} WHERE site = :s AND keyword_id = :k "
                 f"AND {date_col} = :d LIMIT 1"),
            {"s": site, "k": keyword_id, "d": fdate},
        ).first()
        ok = found is not None
        bad += 0 if ok else 1
        logger.info("抽样 %s %r → %s %s：%s", site, keyword, ptype, fdate, "✓" if ok else "✗ 事实行不存在")
    return len(rows), bad


def backfill_site(session, site: str, *, dry_run: bool) -> dict:
    """逐期推进，每期一个事务（一期失败不回滚已完成的期次，便于分批推进）。"""
    periods = list_periods(session, site)
    updated = 0
    for report_date, period in periods:
        n = backfill_period(session, site, period, report_date)
        updated += n
        if dry_run:
            session.rollback()
        else:
            session.commit()
        logger.info("%s %s %s：更新 %d 个词%s", site, period, report_date, n,
                    "（dry-run，已回滚）" if dry_run else "")
    return {"periods": len(periods), "updated": updated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=None,
                        help="站点码，可重复。缺省为全部启用站点")
    parser.add_argument("--dry-run", action="store_true", help="真跑一遍再回滚，不改库")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示（非 dry-run 时需要）")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="完成后每站点抽 N 个词核对最早日期的事实行是否存在")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import text

    from app.site_registry import is_known_site, normalize_site_code
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    from config import settings
    from database import SessionFactory, engine

    engine.echo = False

    requested = args.site or settings.enabled_site_codes
    unknown = [s for s in requested if not is_known_site(s)]
    if unknown:
        logger.error("站点码未登记: %s（英国是 GB 不是 UK）", ", ".join(unknown))
        return 2
    sites = [normalize_site_code(s) for s in requested]

    if not args.dry_run and not args.yes:
        answer = input(f"确认回填 {', '.join(sites)} 的首次入榜记录？可重复执行。[y/N] ")
        if answer.strip().lower() != "y":
            logger.info("已取消")
            return 1

    # 锁必须钉在一条专用连接上并贯穿全程（每期 commit 会换连接，会话级咨询锁跟着连接走）。
    with engine.connect() as lock_conn:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY}).scalar()
        if not got:
            logger.error("有导入正在运行，回填与导入互斥；请稍后重试")
            return 3
        try:
            with SessionFactory() as session:
                for site in sites:
                    before = basis_counts(session, site)
                    stats = backfill_site(session, site, dry_run=args.dry_run)
                    after = basis_counts(session, site)
                    logger.info(
                        "%s 完成：%d 期，更新 %d 个词%s\n  回填前依据分布: %s\n  回填后依据分布: %s",
                        site, stats["periods"], stats["updated"],
                        "（dry-run，未改库）" if args.dry_run else "", before, after,
                    )
                    if after.get("CONFIRMED", 0) > before.get("CONFIRMED", 0):
                        logger.error("%s 出现新增的 CONFIRMED——回填不应产生已确认首次，请排查", site)
                        return 4
                    if args.sample and not args.dry_run:
                        total, bad = sample_check(session, site, args.sample)
                        logger.info("%s 抽样 %d 个，核对不上 %d 个", site, total, bad)
                        if bad:
                            return 5
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
