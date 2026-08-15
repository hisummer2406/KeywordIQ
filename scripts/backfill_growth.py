"""持续增长榜的历史回填：对已导入的日报期次逐期算定榜单。

**上线时必须跑一次。** 榜单只在日报导入收尾时算定，新表建好之后是空的——不回填的话，
库里明明有三个月的日报数据，界面上却要等 7 天才出现第一条榜单，而这期间它看起来就是
「这个功能没有数据」。

**改了 GROWTH_* 阈值之后也要跑。** 算定结果不记录当时的阈值，旧期次仍是旧口径算出来的；
改配置不会让它们跟上，只有重算会。

用法::

    # 先看每期会算出多少行（真跑一遍再回滚，数字是真的，库不变）
    uv run python scripts/backfill_growth.py --dry-run

    # 回填全部启用站点、全部可回填期次
    uv run python scripts/backfill_growth.py --yes

    # 只回填某站点的某段区间
    uv run python scripts/backfill_growth.py --site US --from 2026-07-01 --to 2026-07-15 --yes

**窗口不完整的期次会被跳过并列出**（窗口内有期次未导入时，所有词都凑不满窗口，算出来
必然是空的）。跳过与「算出来是 0 行」在结果上一样，但原因完全不同，故分开报告。

口径本身不在这里——它只有一处定义，即 ``importer.load_growth``。这个脚本负责的是「对哪些
期次调它」。抄一份口径过来迟早与导入侧漂移，而漂移的表现是「回填出来的榜单和每天导入
出来的不一样」，没有任何地方会报错。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# 让脚本直接运行时也能 import 仓库根的模块（约定同 prune_retention.py）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("backfill_growth")


def enumerate_anchors(
    available: list[date],
    window_days: int,
    *,
    since: date | None = None,
    until: date | None = None,
) -> tuple[list[date], list[date]]:
    """把可用期次切成 (可回填, 窗口不完整) 两组，都按期次升序。

    「可回填」的判据是**窗口内每一期都有数据**——差一期就算不出任何一行，因为口径要求
    关键词在窗口内每期都在榜。区间参数只筛锚定日，不影响窗口完整性的判定：回填
    2026-07-15 一天，照样需要 07-09 到 07-15 七期都在。
    """
    present = set(available)
    ready: list[date] = []
    incomplete: list[date] = []
    for anchor in sorted(present):
        if since and anchor < since:
            continue
        if until and anchor > until:
            continue
        window = [anchor - timedelta(days=i) for i in range(window_days)]
        (ready if all(d in present for d in window) else incomplete).append(anchor)
    return ready, incomplete


def _available_dates(session, site: str) -> list[date]:
    from sqlalchemy import text

    rows = session.execute(
        text("SELECT report_date FROM analysis.site_data_stats "
             " WHERE site = :s AND period_type = 'DAY' ORDER BY report_date"),
        {"s": site},
    ).scalars().all()
    return list(rows)


def _backfill_site(session, site: str, anchors: list[date], *, dry_run: bool) -> int:
    """逐期算定，返回写入总行数。

    每期一个事务：一期失败不该把已经算好的前几期一起回滚掉——回填是可以分批推进的运维
    动作，全有或全无只会让人不敢跑第二次。

    dry-run 真跑一遍再回滚：报出来的行数因此是真实的。用估算代替会让「上线前核对」这件
    事失去意义。
    """
    from app.table.analysis.partitioning import ensure_fact_partitions
    from app.table.upload.importer import load_growth

    total = 0
    for anchor in anchors:
        ensure_fact_partitions(session, "fact_growth", site, anchor)
        rows = load_growth(session, site, anchor)
        total += rows
        if dry_run:
            session.rollback()
        else:
            session.commit()
        logger.info("%s %s: %d 行%s", site, anchor, rows, "（dry-run，已回滚）" if dry_run else "")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=None,
                        help="站点码，可重复。缺省为全部启用站点")
    parser.add_argument("--from", dest="since", type=date.fromisoformat, default=None,
                        help="起始锚定日（含），YYYY-MM-DD")
    parser.add_argument("--to", dest="until", type=date.fromisoformat, default=None,
                        help="结束锚定日（含），YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="真跑一遍再回滚，报出每期真实行数但不改库")
    parser.add_argument("--yes", action="store_true",
                        help="跳过确认提示（非 dry-run 时需要）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from app.site_registry import is_known_site, normalize_site_code
    from config import settings
    from database import SessionFactory, engine

    # 本脚本的输出就是运维记录本身，DEBUG=True 时会被几十行 SQL 淹掉（同 prune_retention）。
    engine.echo = False

    # 用 is_known_site 先筛而不是 try/except normalize：命令行传错站点码是操作失误，
    # 该给一句能读的提示，不是一段栈。（而 except 块里的日志按约定必须带栈——那条约定
    # 针对的是真异常，不是参数校验。）
    requested = args.site or settings.enabled_site_codes
    unknown = [s for s in requested if not is_known_site(s)]
    if unknown:
        logger.error("站点码未登记: %s（英国是 GB 不是 UK）", ", ".join(unknown))
        return 2
    sites = [normalize_site_code(s) for s in requested]

    window_days = settings.GROWTH_WINDOW_DAYS
    logger.info(
        "口径: 窗口 %d 期、累计净增 > %d 名、每日环比 ≥ %.2f%%",
        window_days, settings.GROWTH_MIN_RANK_GAIN, settings.GROWTH_MIN_DAILY_RATE * 100,
    )

    plan: dict[str, list[date]] = {}
    with SessionFactory() as session:
        for site in sites:
            ready, incomplete = enumerate_anchors(
                _available_dates(session, site), window_days,
                since=args.since, until=args.until,
            )
            plan[site] = ready
            logger.info("%s: 可回填 %d 期，窗口不完整跳过 %d 期", site, len(ready), len(incomplete))
            if incomplete:
                logger.info("%s 跳过的期次: %s", site, ", ".join(str(d) for d in incomplete))

    total_periods = sum(len(v) for v in plan.values())
    if total_periods == 0:
        logger.info("没有可回填的期次，结束")
        return 0

    if not args.dry_run and not args.yes:
        answer = input(f"确认回填 {total_periods} 期？已算定的同期结果会被重算覆盖。[y/N] ")
        if answer.strip().lower() != "y":
            logger.info("已取消")
            return 1

    written = 0
    with SessionFactory() as session:
        for site, anchors in plan.items():
            written += _backfill_site(session, site, anchors, dry_run=args.dry_run)

    logger.info("完成：%d 期，共 %d 行%s", total_periods, written,
                "（dry-run，未改库）" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
