"""按批次重导历史数据，补齐关键词级的品牌与类目。

品牌与类目从商品维度移出之后，历史分区的六个新列全是 NULL，且**无法从现存数据推导**
——旧模型把品牌#N 与商品#N 硬配后经 `DISTINCT ON (asin)` 去重，名次与关键词的对应关系
在那一步就丢了。唯一的真值来源是 OSS 上的原始对象，凭 `import_batch.object_key` 重导。

**在重导完成之前，品牌与类目筛选在未重导的日期上会返回空结果。** 这是本变更最难解释的
不一致：同一个筛选条件，换个日期就没数据。界面侧对此有明确提示（见 analysis_service
的 `pending_rebuild`），此处的职责是尽快把这个窗口关掉。

用法::

    # 先看会重导哪些批次（不改库，强烈建议每次先跑）
    uv run python scripts/rebuild_brand_category.py --dry-run

    # 全量推进（可随时 Ctrl-C 中断，重跑从断点继续）
    uv run python scripts/rebuild_brand_category.py --yes

    # 只处理某个站点，或限制本轮条数（分批推进、观察负载）
    uv run python scripts/rebuild_brand_category.py --site US --limit 5 --yes

设计要点：

- **可中断续跑**：判据是"该切片的事实行是否已有品牌/类目"，不是脚本自己记的进度文件。
  进度文件会与真实状态漂移（跑一半崩了、或有人手动重导过一批），而库里的数据不会撒谎。
- **串行**：重导走 `run_import`，与常规导入共用 `pg_try_advisory_lock`，天然不会并发。
  拿不到锁的批次置 QUEUED，本脚本把它算作"未完成"，下一轮再来。
- **不删旧数据**：`import_report` 对同一期次是覆盖语义（先删该切片再写），重导是幂等的。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from app.table.upload import oss_client
from app.table.upload.import_batch_model import STATUS_COMPLETED, ImportBatch
from app.table.upload.import_service import run_import
from app.table.upload.import_source import OSSObjectSource
from database import SessionFactory

logger = logging.getLogger("rebuild_brand_category")

# period → (事实表, 日期列)
_FACT = {"DAY": ("fact_daily", "report_date"), "WEEK": ("fact_weekly", "report_date_week")}


def _slice_needs_rebuild(session, site: str, period: str, report_date) -> bool:
    """该切片是否还没有品牌/类目数据。

    判据是**库里的实际状态**而不是脚本记的进度：进度文件会与真实状态漂移（跑一半崩了、
    有人手动重导过一批），而 `top1_brand_id IS NOT NULL` 不会撒谎。

    只看 top1：某个关键词可能确实只有一个品牌，但一整个切片不可能所有关键词的第一品牌
    都为空——除非它就是没重导过。

    **两次 LIMIT 1 探测，不做 count(*)。** 需要回答的只是两个"有没有"，而 count(*) 要
    扫完整个切片（日报 30 万行、周报 300 万行）；候选批次有几百个，全量 --dry-run 会
    因此慢上两个数量级。查询层的同一判断（``crud.slice_has_brand_data_stmt``）本就是
    LIMIT 1，这里跟它保持一致。
    """
    table, date_col = _FACT[period]

    def _exists(extra: str) -> bool:
        return session.execute(
            text(
                f"SELECT 1 FROM analysis.{table} "
                f"WHERE site = :site AND {date_col} = :d {extra} LIMIT 1"
            ),
            {"site": site, "d": report_date},
        ).first() is not None

    if not _exists(""):
        return False  # 切片本身没数据，重导也补不出什么
    return not _exists("AND top1_brand_id IS NOT NULL")


def _pending_batches(session, *, site: str | None, limit: int | None) -> list[ImportBatch]:
    """需要重导的批次：已完成、有 object_key、且对应切片尚无品牌数据。

    同一 (站点, 周期, 日期) 可能有多条批次记录（重导过），只取最新的一条——它对应的
    object_key 才是当前该切片的来源。
    """
    stmt = (
        select(ImportBatch)
        .where(ImportBatch.status == STATUS_COMPLETED, ImportBatch.object_key != "")
        .order_by(ImportBatch.report_date.desc(), ImportBatch.id.desc())
    )
    if site:
        stmt = stmt.where(ImportBatch.site == site)

    seen: set[tuple[str, str, object]] = set()
    out: list[ImportBatch] = []
    for batch in session.execute(stmt).scalars():
        key = (batch.site, batch.period_type, batch.report_date)
        if key in seen:
            continue
        seen.add(key)
        if _slice_needs_rebuild(session, batch.site, batch.period_type, batch.report_date):
            out.append(batch)
            if limit and len(out) >= limit:
                break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="按批次重导历史数据，补齐品牌与类目")
    parser.add_argument("--site", help="只处理该站点")
    parser.add_argument("--limit", type=int, help="本轮最多处理多少个批次")
    parser.add_argument("--dry-run", action="store_true", help="只列出待重导批次，不改库")
    parser.add_argument("--yes", action="store_true", help="确认执行")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.dry_run and not args.yes:
        parser.error("执行需要 --yes；建议先 --dry-run 核对")

    with SessionFactory() as session:
        pending = _pending_batches(session, site=args.site, limit=args.limit)

    if not pending:
        logger.info("没有待重导的批次——历史数据的品牌与类目已补齐")
        return 0

    logger.info("待重导 %d 个批次：", len(pending))
    for b in pending:
        logger.info("  %s %s %s  %s", b.site, b.period_type, b.report_date, b.file_name)

    if args.dry_run:
        logger.info("(--dry-run，未改动任何数据)")
        return 0

    ok = failed = 0
    for b in pending:
        logger.info("重导 %s %s %s ...", b.site, b.period_type, b.report_date)
        try:
            batch = run_import(
                site=b.site,
                period=b.period_type,
                report_date=b.report_date,
                source=OSSObjectSource(b.object_key),
                original_filename=b.file_name,
                object_key=b.object_key,
            )
        except oss_client.OSSObjectNotFound:
            # 对象被清理掉了：这个切片再也补不回来，必须让人看见而不是当成一次普通失败
            logger.error("  OSS 对象已不存在，该切片无法重建：%s %s", b.site, b.report_date)
            failed += 1
            continue
        except Exception:
            logger.exception("  重导异常")
            failed += 1
            continue

        if batch.status == STATUS_COMPLETED:
            logger.info("  完成，写入 %d 行", batch.written_rows)
            ok += 1
        else:
            # 多半是没抢到导入串行锁（置 QUEUED），下一轮会再来，不算错误
            logger.warning("  未完成（status=%s），下一轮重试", batch.status)
            failed += 1

    logger.info("本轮完成 %d，未完成 %d", ok, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
