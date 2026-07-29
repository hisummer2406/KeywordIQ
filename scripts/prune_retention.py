"""保留清理入口：只保留最近若干自然月的事实数据，移出更早的月度分区。

这是可定时触发的运维入口，不在 FastAPI 事件循环内运行（DDL 会持锁）。由 cron / 容器
定时任务在低峰调用即可。清理幂等——补跑不会重复移出，也不会误伤窗口内数据。

用法::

    # 先看会移出哪些分区（不改库，强烈建议每次先跑）
    uv run python scripts/prune_retention.py --dry-run

    # 按配置的 HOT_WINDOW_MONTHS 执行清理
    uv run python scripts/prune_retention.py --yes

    # 移出前把超期分区导出留档，并临时覆盖保留月数
    uv run python scripts/prune_retention.py --export-dir archive/pruned --retention-months 3 --yes

首次上线的存量一次性清理也走这个入口：它会移出**所有**超期月份，存量场景一次会命中更多
月份，务必先 ``--dry-run`` 核对、并配合 ``--export-dir`` 留档。

cron 示例（每天凌晨 3 点，容器内）::

    0 3 * * *  cd /app && uv run python scripts/prune_retention.py --yes >> logs/prune.log 2>&1
"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger("prune_retention")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出将移出的分区，不导出、不执行、不提交")
    parser.add_argument("--retention-months", type=int, default=None,
                        help="覆盖 HOT_WINDOW_MONTHS，保留最近多少个自然月")
    parser.add_argument("--export-dir", type=str, default=None,
                        help="移出前把每个超期分区导出为 <分区名>.csv 到该目录")
    parser.add_argument("--yes", action="store_true",
                        help="跳过确认提示（非 dry-run 时需要）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from database import SessionFactory
    from app.table.analysis import retention_service

    with SessionFactory() as session:
        # 先做一次 dry-run 枚举，把将移出的分区摆到用户面前再决定是否执行。
        preview = retention_service.run_retention(
            session,
            retention_months=args.retention_months,
            dry_run=True,
        )

    logger.info("保留下界 cutoff=%s（保留月数=%s），报告日期早于此的分区将被移出",
                preview.cutoff, preview.retention_months)
    for table_name, names in preview.dropped.items():
        if names:
            logger.info("%s 将移出 %s 个分区: %s", table_name, len(names), ", ".join(names))
        else:
            logger.info("%s 无超期分区", table_name)

    if preview.total_dropped == 0:
        logger.info("没有需要移出的分区，结束")
        return 0

    if args.dry_run:
        logger.info("dry-run 结束，未做任何变更")
        return 0

    if not args.yes:
        answer = input(f"确认移出上述 {preview.total_dropped} 个分区？此操作不可回滚。[y/N] ")
        if answer.strip().lower() != "y":
            logger.info("已取消")
            return 1

    with SessionFactory() as session:
        result = retention_service.run_retention(
            session,
            retention_months=args.retention_months,
            export_dir=args.export_dir,
            dry_run=False,
        )

    logger.info("完成，共移出 %s 个分区", result.total_dropped)
    if result.exported:
        for table_name, paths in result.exported.items():
            for path in paths:
                logger.info("已留档: %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
