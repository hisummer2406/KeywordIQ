"""为已导入的切片补算品牌命中对（``keyword_brand_hit``），并把切片置为就绪。

迁移 ``b7d1e4a9c2f3`` 之前导入的切片 ``brand_hits_ready`` 都是假，排除品牌词对它们仍逐行判定
（结果相同、更慢）。新导入在收尾时自己算，只有存量切片需要本脚本。判定复用
``app.table.analysis.brand_hits.rebuild_slice``，与导入是同一份实现。

按期次**倒序**处理（最近的期次最常被查），每个切片一个事务：中断后重跑只处理仍未就绪的切片。
可重复执行；已就绪的切片不再处理。

用法::

    # 只列出待处理切片，不改库
    uv run python scripts/backfill_brand_hits.py --dry-run

    # 全部站点与周期
    uv run python scripts/backfill_brand_hits.py --yes

    # 只补 US 周报的最近 3 期
    uv run python scripts/backfill_brand_hits.py --site US --view WEEK --limit 3 --yes

持有导入的咨询锁：与导入互斥（两者都写命中表、都改就绪标记），拿不到锁就退出，不排队。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("backfill_brand_hits")


def pending_slices(session, *, site: str | None, view: str | None, limit: int | None):
    """未就绪的切片 (site, period, report_date)，期次倒序。"""
    from sqlalchemy import text

    sql = ("SELECT site, period_type, report_date FROM analysis.site_data_stats "
           " WHERE NOT brand_hits_ready")
    params: dict = {}
    if site:
        sql += " AND site = :site"
        params["site"] = site
    if view:
        sql += " AND period_type = :view"
        params["view"] = view
    sql += " ORDER BY report_date DESC, period_type, site"
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return [tuple(r) for r in session.execute(text(sql), params).all()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", help="站点码，缺省为全部")
    parser.add_argument("--view", choices=("DAY", "WEEK"), help="周期，缺省为全部")
    parser.add_argument("--limit", type=int, help="最多处理的切片数")
    parser.add_argument("--dry-run", action="store_true", help="只列出待处理切片，不改库")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示（非 dry-run 时需要）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import text

    from app.site_registry import is_known_site, normalize_site_code
    from app.table.analysis.brand_hits import rebuild_slice
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    from database import SessionFactory, engine

    engine.echo = False
    site = None
    if args.site:
        if not is_known_site(args.site):
            logger.error("站点码未登记: %s（英国是 GB 不是 UK）", args.site)
            return 2
        site = normalize_site_code(args.site)

    with SessionFactory() as session:
        slices = pending_slices(session, site=site, view=args.view, limit=args.limit)
    logger.info("待处理切片 %d 个%s", len(slices), "（dry-run，不改库）" if args.dry_run else "")
    for s in slices:
        logger.info("  %s %s %s", *s)
    if args.dry_run or not slices:
        return 0
    if not args.yes:
        answer = input(f"确认为 {len(slices)} 个切片补算品牌命中对？可重复执行。[y/N] ")
        if answer.strip().lower() != "y":
            logger.info("已取消")
            return 1

    # 锁钉在专用连接上贯穿全程（每个切片 commit 会换连接，会话级咨询锁跟着连接走）。
    with engine.connect() as lock_conn:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY}).scalar()
        if not got:
            logger.error("有导入正在运行，回填与导入互斥；请稍后重试")
            return 3
        try:
            with SessionFactory() as session:
                total = 0
                for s_site, period, report_date in slices:
                    started = time.monotonic()
                    added = rebuild_slice(session, site=s_site, period=period, report_date=report_date)
                    session.commit()
                    total += added
                    logger.info("%s %s %s：新增 %d 对，%.1f s", s_site, period, report_date, added,
                                time.monotonic() - started)
                logger.info("完成：%d 个切片，新增 %d 对", len(slices), total)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
