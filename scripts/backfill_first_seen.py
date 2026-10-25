"""存量关键词的首次入榜回填：用库内仍保留的事实数据，把能证明的首次记录补上。

迁移只加列，既有关键词全是 ``UNKNOWN`` 且日期为空；不回填的话，它们会在各自下一次出现时
被导入一个一个顺手补成 ``EARLIEST_OBSERVED``，那是一次 UPDATE 风暴（每个被改的词都要在
``keyword`` 的 GIN 索引上插新条目），所以集中做。两种模式：

- **常规回填（缺省）只产出 ``EARLIEST_OBSERVED``。** 事实表只留最近三个月，库内最早的一条
  记录不一定是这个词的历史首次——更早的可能早被清理。把它写成导入创建，上线日附近的词
  就会被当成真的首次入榜，而没有任何报错；它也不移动站点监测起点（只取 ``IMPORTED`` 行）。
- **证据重放（``--replay``）**：仅当证据完整性校验通过（各周期事实表最早期次等于最早已完成
  导入批次、每个已完成批次的事实都还在、且都登记在 ``site_data_stats``），按期次重放：
  站点最早一期（B0）的词记 ``EARLIEST_OBSERVED``，之后才第一次出现的词记 ``IMPORTED``。
  校验不通过直接拒绝，不降级成猜测。代价：新的监测起点落在 B0 之后第一期，该期仍显示
  「无法判断」（keyword-first-seen spec；#143 生产即如此，三站 B 均为 2026-09-14）。

口径只有一处：比较键（期次日期，日报优先于周报）与导入共用 ``importer.first_seen_advance_sql``，
按期次**升序**逐期推进（升序时每个词只在第一次出现的那期被改一次，之后的期次因为更晚而
零更新；降序则每个更早的期次都会改写一遍）。可重复运行，第二次零更新。

每次正式运行把被改动词的原状态与新状态记进普通表 ``analysis.bak_first_seen_<时间>``
（不用临时表：会话断开即丢）。``--undo`` 只恢复仍等于回填写入值的词，回填之后又被导入
修正过的词保持新值。用法::

    # 真跑一遍再回滚，数字是真的，库不变
    uv run python scripts/backfill_first_seen.py --replay --dry-run

    # 全部启用站点证据重放，并每站点抽 20 个词核对记录等于事实表的最早期次
    uv run python scripts/backfill_first_seen.py --replay --yes --sample 20

    # 撤销某次回填
    uv run python scripts/backfill_first_seen.py --undo bak_first_seen_20261025t120000 --yes

脚本持有导入的咨询锁：回填与导入互斥，避免与导入同时全库扫描抢 IO（PostgreSQL 与应用同机）；
拿不到锁（有导入在跑）就退出，不排队。
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("backfill_first_seen")

_TABLES = {"DAY": ("fact_daily", "report_date"), "WEEK": ("fact_weekly", "report_date_week")}
_BACKUP_NAME = re.compile(r"^bak_first_seen_[a-z0-9_]+$")


def _rank() -> dict[str, int]:
    from app.table.upload.importer import FIRST_SEEN_PERIOD_RANK

    return FIRST_SEEN_PERIOD_RANK


def list_periods(session, site: str) -> list[tuple[date, str]]:
    """站点库内仍有事实数据的 (期次日期, 周期)，按（日期升序，日报先于周报）。

    来源是 ``site_data_stats``——事实表上做 DISTINCT 要扫一遍几千万行。保留清理会同步清理
    它的残留行，所以这里列出的期次就是仍在库内的期次。
    """
    from sqlalchemy import text

    rank = _rank()
    rows = session.execute(
        text("SELECT report_date, period_type FROM analysis.site_data_stats WHERE site = :s"),
        {"s": site},
    ).all()
    return sorted(((r[0], r[1]) for r in rows), key=lambda x: (x[0], rank[x[1]]))


def evidence_gaps(session, site: str) -> list[str]:
    """证据完整性校验：返回不能重放的原因，空列表才算通过。只读。

    「已完成批次」只算写入过事实的（``written_rows > 0``）。保留清理整月 DROP 事实分区，但
    不删 ``import_batch``，所以「已完成批次的事实不在了」就是被清理的直接证据。
    """
    from sqlalchemy import text

    from app.table.upload.import_batch_model import STATUS_COMPLETED

    gaps: list[str] = []
    stats = set(list_periods(session, site))
    if not stats:
        return [f"{site} 没有任何期次的事实数据"]
    for period, (table, date_col) in _TABLES.items():
        batch_dates = [r[0] for r in session.execute(
            text("SELECT DISTINCT report_date FROM analysis.import_batch "
                 " WHERE site = :s AND period_type = :p AND status = :st AND written_rows > 0 "
                 " ORDER BY 1"),
            {"s": site, "p": period, "st": STATUS_COMPLETED},
        ).all()]
        fact_min = session.execute(
            text(f"SELECT min({date_col}) FROM analysis.{table} WHERE site = :s"), {"s": site}
        ).scalar()
        batch_min = batch_dates[0] if batch_dates else None
        if fact_min != batch_min:
            gaps.append(f"{site} {period} 事实表最早期次 {fact_min} ≠ 最早已完成批次 {batch_min}")
        missing_facts = [
            d for d in batch_dates
            if session.execute(
                text(f"SELECT 1 FROM analysis.{table} WHERE site = :s AND {date_col} = :d LIMIT 1"),
                {"s": site, "d": d},
            ).first() is None
        ]
        if missing_facts:
            gaps.append(f"{site} {period} 有已完成批次的事实已不在库内（被清理或导入异常）："
                        f"{_brief(missing_facts)}")
        unlisted = [d for d in batch_dates if (d, period) not in stats]
        if unlisted:
            gaps.append(f"{site} {period} 有已完成批次未登记在 site_data_stats，重放会漏掉："
                        f"{_brief(unlisted)}")
    return gaps


def _brief(dates: list[date]) -> str:
    head = ", ".join(str(d) for d in dates[:5])
    return head + (f" 等 {len(dates)} 期" if len(dates) > 5 else "")


def new_backup_name() -> str:
    return "bak_first_seen_" + datetime.now().strftime("%Y%m%dt%H%M%S")


def ensure_backup_table(session, name: str) -> None:
    """列与 #143 手工回填的 ``analysis.bak_first_seen_143`` 相同，``--undo`` 两者通用。"""
    from sqlalchemy import text

    _check_backup_name(name)
    session.execute(text(
        f"""
        CREATE TABLE IF NOT EXISTS analysis.{name} (
            keyword_id bigint PRIMARY KEY,
            site varchar(8) NOT NULL,
            old_date date, old_period varchar(4), old_basis varchar(20) NOT NULL,
            new_date date, new_period varchar(4), new_basis varchar(20) NOT NULL
        )
        """
    ))


def _check_backup_name(name: str) -> None:
    if not _BACKUP_NAME.match(name):
        raise ValueError(f"回滚表名须匹配 {_BACKUP_NAME.pattern}：{name!r}")


def backfill_period(
    session, site: str, period: str, report_date: date, *, backup: str, b0: date | None = None
) -> int:
    """把一期的事实数据当作证据推进首次记录，返回被修改的词数。不提交。

    ``b0`` 给出时是证据重放：``UNKNOWN`` 词在 B0 那天见到记最早可观测，晚于 B0 记导入创建。
    改动的词把**原状态**（``o`` 是语句开始时的快照）与新状态一并记进回滚表；同一个词被多期
    改动时保留最初的原状态、更新为最后的新状态。
    """
    from sqlalchemy import text

    from app.table.upload.importer import first_seen_advance_sql

    table, date_col = _TABLES[period]
    unknown_basis = (
        "CASE WHEN CAST(:rdate AS date) = CAST(:b0 AS date)"
        " THEN 'EARLIEST_OBSERVED' ELSE 'IMPORTED' END"
        if b0 is not None else "'EARLIEST_OBSERVED'"
    )
    update = first_seen_advance_sql(
        f"analysis.{table} f JOIN analysis.dim_keyword o ON o.keyword_id = f.keyword_id",
        f"f.site = :site AND f.{date_col} = :rdate AND k.keyword_id = f.keyword_id"
        " AND o.keyword_id = k.keyword_id",
        unknown_basis=unknown_basis,
        returning="RETURNING k.keyword_id, k.site, o.first_seen_date AS old_date,"
                  " o.first_seen_period_type AS old_period, o.first_seen_basis AS old_basis,"
                  " k.first_seen_date AS new_date, k.first_seen_period_type AS new_period,"
                  " k.first_seen_basis AS new_basis",
    )
    _check_backup_name(backup)
    result = session.execute(
        text(
            f"""
            WITH changed AS ({update})
            INSERT INTO analysis.{backup}
                   (keyword_id, site, old_date, old_period, old_basis, new_date, new_period, new_basis)
            SELECT keyword_id, site, old_date, old_period, old_basis, new_date, new_period, new_basis
              FROM changed
            ON CONFLICT (keyword_id) DO UPDATE
               SET new_date = EXCLUDED.new_date, new_period = EXCLUDED.new_period,
                   new_basis = EXCLUDED.new_basis
            """
        ),
        {"site": site, "period": period, "rdate": report_date, "rank": _rank()[period], "b0": b0},
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


def monitoring_start(session, site: str) -> date | None:
    """站点监测起点 B：``IMPORTED`` 行的最小首次日期（走部分索引）。"""
    from sqlalchemy import text

    return session.execute(
        text("SELECT min(first_seen_date) FROM analysis.dim_keyword "
             " WHERE site = :s AND first_seen_basis = 'IMPORTED'"),
        {"s": site},
    ).scalar()


def sample_check(session, site: str, n: int, *, bases: tuple[str, ...] = ("EARLIEST_OBSERVED",)
                 ) -> tuple[int, int]:
    """每种依据随机抽 ``n`` 个词，核对记录的 (日期, 周期) 等于事实表里该词的最早期次。
    返回 (抽到的数量, 核对不上的数量)——后者应为 0。

    常规回填只抽最早可观测：清理过分区后，导入创建词的真实首次可能已不在事实表里。"""
    from sqlalchemy import text

    rank = _rank()
    total = bad = 0
    for basis in bases:
        rows = session.execute(
            text(
                "SELECT keyword_id, keyword, first_seen_date, first_seen_period_type "
                "  FROM analysis.dim_keyword "
                " WHERE site = :s AND first_seen_basis = :b AND first_seen_date IS NOT NULL "
                " ORDER BY random() LIMIT :n"
            ),
            {"s": site, "b": basis, "n": n},
        ).all()
        for keyword_id, keyword, fdate, ptype in rows:
            earliest = None
            for period, (table, date_col) in _TABLES.items():
                d = session.execute(
                    text(f"SELECT min({date_col}) FROM analysis.{table} "
                         f" WHERE site = :s AND keyword_id = :k"),
                    {"s": site, "k": keyword_id},
                ).scalar()
                if d is not None and (earliest is None or (d, rank[period]) < earliest):
                    earliest = (d, rank[period])
            ok = earliest == (fdate, rank[ptype])
            total += 1
            bad += 0 if ok else 1
            logger.info("抽样 %s %s %r → %s %s：%s", site, basis, keyword, ptype, fdate,
                        "✓" if ok else f"✗ 事实表最早为 {earliest}")
    return total, bad


def backfill_site(session, site: str, *, dry_run: bool, replay: bool = False,
                  backup: str | None = None) -> dict:
    """逐期推进。正式跑每期一个事务（一期失败不回滚已完成的期次，便于分批推进；回滚表与
    当期更新同一事务，二者不会错位）。

    dry-run 则**所有期次留在同一个事务里，最后只回滚一次**：逐期回滚会把前面期次的模拟
    更新一并撤销，下一期从生产原状态重新开始，同一个词出现在多期就被重复计入，报出的
    ``updated`` 虚高，也无法预览正式跑产出的依据分布（Codex 评审 PR #141 P2）。

    ``replay`` 时先做证据完整性校验，不通过抛 ``EvidenceIncomplete``，一行不改。
    """
    if replay:
        gaps = evidence_gaps(session, site)
        if gaps:
            raise EvidenceIncomplete(gaps)
    backup = backup or new_backup_name()
    ensure_backup_table(session, backup)
    periods = list_periods(session, site)
    b0 = periods[0][0] if replay and periods else None
    updated = 0
    for report_date, period in periods:
        n = backfill_period(session, site, period, report_date, backup=backup, b0=b0)
        updated += n
        if not dry_run:
            session.commit()
        logger.info("%s %s %s：更新 %d 个词%s", site, period, report_date, n,
                    "（dry-run，最后统一回滚）" if dry_run else "")
    if dry_run:
        session.rollback()
    return {"periods": len(periods), "updated": updated, "b0": b0, "backup": backup}


def undo(session, backup: str) -> int:
    """按回滚表恢复原状态，只动仍等于回填写入值的词。返回恢复的词数。不提交。"""
    from sqlalchemy import text

    _check_backup_name(backup)
    return session.execute(text(
        f"""
        UPDATE analysis.dim_keyword k
           SET first_seen_date = b.old_date, first_seen_period_type = b.old_period,
               first_seen_basis = b.old_basis
          FROM analysis.{backup} b
         WHERE k.keyword_id = b.keyword_id
           AND k.first_seen_date IS NOT DISTINCT FROM b.new_date
           AND k.first_seen_period_type IS NOT DISTINCT FROM b.new_period
           AND k.first_seen_basis = b.new_basis
        """
    )).rowcount


class EvidenceIncomplete(Exception):
    def __init__(self, gaps: list[str]):
        super().__init__("；".join(gaps))
        self.gaps = gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=None,
                        help="站点码，可重复。缺省为全部启用站点")
    parser.add_argument("--replay", action="store_true",
                        help="证据完整时按期次重放，B0 之后首次出现的词记导入创建；校验不过则拒绝")
    parser.add_argument("--undo", metavar="TABLE", help="按回滚表撤销一次回填（analysis 下的表名）")
    parser.add_argument("--dry-run", action="store_true", help="真跑一遍再回滚，不改库")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示（非 dry-run 时需要）")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="完成后每站点每种依据抽 N 个词核对记录等于事实表最早期次")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import text

    from app.site_registry import is_known_site, normalize_site_code
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    from config import settings
    from database import SessionFactory, engine

    engine.echo = False

    if args.undo:
        if not _BACKUP_NAME.match(args.undo):
            logger.error("回滚表名须匹配 %s：%r", _BACKUP_NAME.pattern, args.undo)
            return 2
    else:
        requested = args.site or settings.enabled_site_codes
        unknown = [s for s in requested if not is_known_site(s)]
        if unknown:
            logger.error("站点码未登记: %s（英国是 GB 不是 UK）", ", ".join(unknown))
            return 2
        sites = [normalize_site_code(s) for s in requested]

    if not args.dry_run and not args.yes:
        what = f"按 {args.undo} 撤销回填" if args.undo else (
            f"{'证据重放' if args.replay else '常规'}回填 {', '.join(sites)} 的首次入榜记录")
        answer = input(f"确认{what}？[y/N] ")
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
                if args.undo:
                    n = undo(session, args.undo)
                    if args.dry_run:
                        session.rollback()
                    else:
                        session.commit()
                    logger.info("恢复 %d 个词%s；确认无误后可 DROP TABLE analysis.%s", n,
                                "（dry-run，未改库）" if args.dry_run else "", args.undo)
                    return 0
                if args.replay:
                    # 先校验全部站点再动手，免得前一个站点已改、后一个才发现不能重放。
                    gaps = [g for site in sites for g in evidence_gaps(session, site)]
                    if gaps:
                        for g in gaps:
                            logger.error("证据不完整，拒绝重放：%s", g)
                        return 6
                backup = new_backup_name()
                for site in sites:
                    before = basis_counts(session, site)
                    stats = backfill_site(session, site, dry_run=args.dry_run,
                                          replay=args.replay, backup=backup)
                    after = basis_counts(session, site)
                    logger.info(
                        "%s 完成：%d 期，更新 %d 个词%s\n  回填前依据分布: %s\n  回填后依据分布: %s"
                        "\n  仍未知: %d；B0: %s；监测起点 B: %s",
                        site, stats["periods"], stats["updated"],
                        "（dry-run，未改库）" if args.dry_run else "", before, after,
                        after.get("UNKNOWN", 0), stats["b0"], monitoring_start(session, site),
                    )
                    if not args.replay and after.get("IMPORTED", 0) > before.get("IMPORTED", 0):
                        logger.error("%s 出现新增的 IMPORTED——常规回填不应产生导入创建的词，请排查", site)
                        return 4
                    if args.sample and not args.dry_run:
                        bases = ("EARLIEST_OBSERVED", "IMPORTED") if args.replay else ("EARLIEST_OBSERVED",)
                        total, bad = sample_check(session, site, args.sample, bases=bases)
                        logger.info("%s 抽样 %d 个，核对不上 %d 个", site, total, bad)
                        if bad:
                            return 5
                if not args.dry_run:
                    logger.info("原状态已记入 analysis.%s；撤销：--undo %s", backup, backup)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
