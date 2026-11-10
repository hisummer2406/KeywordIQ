"""周期首次证据回填（#178）：按事实重放每个词的「首次进入日榜 / 周榜」证据。

迁移只加列，既有词两周期全是 ``UNKNOWN``；导入只会把它们推进成 ``EARLIEST_OBSERVED``（存量词
不升级），所以「先上周榜、后上日榜」的存量词永远等不到「首次进入日榜」——必须按证据重放。

**只在证据完整时执行**（keyword-first-seen「周期首次证据可按证据重放回填」）。复用
``backfill_first_seen.evidence_gaps``：各周期事实表最早期次 = 最早已完成批次、每个已完成批次的
事实都还在、且都登记在 ``site_data_stats``。任一站点不通过即整体拒绝，不降级成猜测——尤其不能
写 ``ABSENT``：那是「确知没见过」，证据缺一期就可能把见过的词判成没见过。

集合式重算（design D7），与逐期导入等价的前提正是证据完整：

- 该周期有事实：日期 = 最早期次；晚于该周期最早期次 ``B0_P`` 记 ``IMPORTED``，等于记
  ``EARLIEST_OBSERVED``（``B0_P`` 当期本就不可判断，与 #143 重放同一取舍）；
- 该周期没有任何事实：``ABSENT``，日期为空。

两个周期合成一条 UPDATE（每行至多改写一次，``dim_keyword`` 挂着 trigram GIN，每次 UPDATE 都插新
索引条目），``IS DISTINCT FROM`` 守卫，第二次运行零更新。**共享首次记录与事实表不动。**

每次正式运行把被改动行的原四列与新四列记进普通表 ``analysis.bak_period_first_<时间>``；``--undo``
只恢复仍等于回填写入值的行，回填之后又被导入改过的行保持新值。用法::

    # 真跑一遍再回滚：各周期各等级数量、B_P 都是真的，库不变
    uv run python scripts/backfill_period_first.py --dry-run

    # 全部启用站点正式回填，并每站点每周期抽 20 个导入创建的词与事实表核对
    uv run python scripts/backfill_period_first.py --yes --sample 20

    # 撤销
    uv run python scripts/backfill_period_first.py --undo bak_period_first_20261110t120000_123456 --yes

持有导入的咨询锁：与导入互斥（同机 PostgreSQL，避免与导入同时整表扫描抢 IO），拿不到即退出。
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_first_seen import EvidenceIncomplete, evidence_gaps

logger = logging.getLogger("backfill_period_first")

_BACKUP_NAME = re.compile(r"^bak_period_first_[a-z0-9_]+$")
_FACTS = {"day": ("fact_daily", "report_date"), "week": ("fact_weekly", "report_date_week")}
_COLS = ("day_first_date", "day_first_basis", "week_first_date", "week_first_basis")


def new_backup_name() -> str:
    # 精确到微秒：同一秒内两次运行共用一张表，撤销会把前一次的改动一起恢复。
    return "bak_period_first_" + datetime.now().strftime("%Y%m%dt%H%M%S_%f")


def _check_backup_name(name: str) -> None:
    if not _BACKUP_NAME.match(name):
        raise ValueError(f"回滚表名须匹配 {_BACKUP_NAME.pattern}：{name!r}")


def ensure_backup_table(session, name: str) -> None:
    from sqlalchemy import text

    _check_backup_name(name)
    session.execute(text(
        f"""
        CREATE TABLE IF NOT EXISTS analysis.{name} (
            keyword_id bigint PRIMARY KEY,
            site varchar(8) NOT NULL,
            old_day_date date, old_day_basis varchar(20) NOT NULL,
            old_week_date date, old_week_basis varchar(20) NOT NULL,
            new_day_date date, new_day_basis varchar(20) NOT NULL,
            new_week_date date, new_week_basis varchar(20) NOT NULL
        )
        """
    ))


def period_baselines(session, site: str) -> dict[str, object]:
    """各周期最早事实期次 ``B0_P``；该周期没有事实时为 None（全部词记 ABSENT）。"""
    from sqlalchemy import text

    return {
        prefix: session.execute(
            text(f"SELECT min({col}) FROM analysis.{table} WHERE site = :s"), {"s": site}
        ).scalar()
        for prefix, (table, col) in _FACTS.items()
    }


def _target_sql() -> str:
    """每个词的目标四列（只读子查询），回填与抽样核对共用。参数 ``:s`` ``:b0_day`` ``:b0_week``。"""
    def basis(alias: str, b0: str) -> str:
        return (f"CASE WHEN {alias}.dt IS NULL THEN 'ABSENT'"
                f" WHEN {alias}.dt > CAST(:{b0} AS date) THEN 'IMPORTED'"
                f" ELSE 'EARLIEST_OBSERVED' END")

    return f"""
        SELECT k.keyword_id,
               d.dt AS day_first_date, {basis('d', 'b0_day')} AS day_first_basis,
               w.dt AS week_first_date, {basis('w', 'b0_week')} AS week_first_basis
          FROM analysis.dim_keyword k
          LEFT JOIN (SELECT keyword_id, min(report_date) AS dt FROM analysis.fact_daily
                      WHERE site = :s GROUP BY keyword_id) d ON d.keyword_id = k.keyword_id
          LEFT JOIN (SELECT keyword_id, min(report_date_week) AS dt FROM analysis.fact_weekly
                      WHERE site = :s GROUP BY keyword_id) w ON w.keyword_id = k.keyword_id
         WHERE k.site = :s
    """


def backfill_site(session, site: str, *, backup: str) -> dict:
    """单站点重算并写入，不提交。调用方先做证据校验；这里再校验一次，防止被单独调用时绕过。"""
    from sqlalchemy import text

    gaps = evidence_gaps(session, site)
    if gaps:
        raise EvidenceIncomplete(gaps)
    ensure_backup_table(session, backup)
    b0 = period_baselines(session, site)
    cols = ", ".join(_COLS)
    updated = session.execute(text(
        f"""
        WITH t AS ({_target_sql()}),
        changed AS (
            UPDATE analysis.dim_keyword k
               SET day_first_date = t.day_first_date, day_first_basis = t.day_first_basis,
                   week_first_date = t.week_first_date, week_first_basis = t.week_first_basis
              FROM t, analysis.dim_keyword o
             WHERE k.keyword_id = t.keyword_id AND o.keyword_id = k.keyword_id
               AND ({', '.join('k.' + c for c in _COLS)})
                   IS DISTINCT FROM ({', '.join('t.' + c for c in _COLS)})
            RETURNING k.keyword_id, k.site,
                      {', '.join('o.' + c for c in _COLS)}, {', '.join('k.' + c + ' AS n_' + c for c in _COLS)}
        )
        INSERT INTO analysis.{backup}
               (keyword_id, site, old_day_date, old_day_basis, old_week_date, old_week_basis,
                new_day_date, new_day_basis, new_week_date, new_week_basis)
        SELECT keyword_id, site, {cols}, {', '.join('n_' + c for c in _COLS)} FROM changed
        ON CONFLICT (keyword_id) DO UPDATE
           SET new_day_date = EXCLUDED.new_day_date, new_day_basis = EXCLUDED.new_day_basis,
               new_week_date = EXCLUDED.new_week_date, new_week_basis = EXCLUDED.new_week_basis
        """
    ), {"s": site, "b0_day": b0["day"], "b0_week": b0["week"]}).rowcount
    return {"updated": updated, "b0": b0}


def basis_counts(session, site: str) -> dict[str, dict[str, int]]:
    from sqlalchemy import text

    out: dict[str, dict[str, int]] = {}
    for prefix in _FACTS:
        rows = session.execute(text(
            f"SELECT {prefix}_first_basis, count(*) FROM analysis.dim_keyword"
            f" WHERE site = :s GROUP BY 1 ORDER BY 1"
        ), {"s": site}).all()
        out[prefix] = {r[0]: r[1] for r in rows}
    return out


def period_starts(session, site: str) -> dict[str, object]:
    """周期监测起点 ``B_P``（与查询同一口径）。"""
    from app.table.analysis import analysis_crud as crud

    return {
        prefix: session.execute(crud.period_imported_since_stmt(site, view)).scalar()
        for prefix, view in (("day", "DAY"), ("week", "WEEK"))
    }


def sample_check(session, site: str, n: int) -> tuple[int, int]:
    """每周期随机抽 n 个 IMPORTED 词，核对日期等于该周期事实表最早期次。返回 (抽样数, 不符数)。"""
    from sqlalchemy import text

    total = bad = 0
    for prefix, (table, col) in _FACTS.items():
        rows = session.execute(text(
            f"""
            SELECT k.{prefix}_first_date,
                   (SELECT min(f.{col}) FROM analysis.{table} f
                     WHERE f.site = :s AND f.keyword_id = k.keyword_id)
              FROM analysis.dim_keyword k
             WHERE k.site = :s AND k.{prefix}_first_basis = 'IMPORTED'
             ORDER BY random() LIMIT :n
            """
        ), {"s": site, "n": n}).all()
        total += len(rows)
        bad += sum(1 for got, want in rows if got != want)
    return total, bad


def undo(session, backup: str) -> int:
    """按回滚表恢复原状态，只动仍等于回填写入值的行。返回恢复的行数。不提交。"""
    from sqlalchemy import text

    _check_backup_name(backup)
    return session.execute(text(
        f"""
        UPDATE analysis.dim_keyword k
           SET day_first_date = b.old_day_date, day_first_basis = b.old_day_basis,
               week_first_date = b.old_week_date, week_first_basis = b.old_week_basis
          FROM analysis.{backup} b
         WHERE k.keyword_id = b.keyword_id
           AND k.day_first_date IS NOT DISTINCT FROM b.new_day_date
           AND k.day_first_basis = b.new_day_basis
           AND k.week_first_date IS NOT DISTINCT FROM b.new_week_date
           AND k.week_first_basis = b.new_week_basis
        """
    )).rowcount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=None,
                        help="站点码，可重复。缺省为全部启用站点")
    parser.add_argument("--undo", metavar="TABLE", help="按回滚表撤销一次回填（analysis 下的表名）")
    parser.add_argument("--dry-run", action="store_true", help="真跑一遍再回滚，不改库")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示（非 dry-run 时需要）")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="完成后每站点每周期抽 N 个导入创建的词，核对日期等于事实表最早期次")
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
        what = f"按 {args.undo} 撤销回填" if args.undo else f"回填 {', '.join(sites)} 的周期首次证据"
        if input(f"确认{what}？[y/N] ").strip().lower() != "y":
            logger.info("已取消")
            return 1

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
                # 先校验全部站点再动手，免得前一个站点已改、后一个才发现不能重放。
                gaps = [g for site in sites for g in evidence_gaps(session, site)]
                if gaps:
                    for g in gaps:
                        logger.error("证据不完整，拒绝回填：%s", g)
                    return 6
                backup = new_backup_name()
                for site in sites:
                    before = basis_counts(session, site)
                    stats = backfill_site(session, site, backup=backup)
                    after = basis_counts(session, site)
                    starts = period_starts(session, site)
                    logger.info(
                        "%s 完成：更新 %d 个词%s\n  回填前: %s\n  回填后: %s\n  B0: %s；B_P: %s",
                        site, stats["updated"], "（dry-run，最后回滚）" if args.dry_run else "",
                        before, after, stats["b0"], starts,
                    )
                    if args.sample:
                        total, bad = sample_check(session, site, args.sample)
                        logger.info("%s 抽样 %d 个，核对不上 %d 个", site, total, bad)
                        if bad:
                            session.rollback()
                            return 5
                    if args.dry_run:
                        session.rollback()
                    else:
                        session.commit()
                if not args.dry_run:
                    logger.info("原状态已记入 analysis.%s；撤销：--undo %s", backup, backup)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
