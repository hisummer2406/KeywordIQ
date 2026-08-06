"""实测掉榜集合算定的耗时（design.md 的 Open Questions 1/2）。

按报告日期顺序导入若干真实体量报表，逐期记录 `_load_dropped` 的墙钟耗时与产出行数。
不是生产代码路径的一部分，只在需要回填实测数字时手动跑。

用法::

    DATABASE_URL=... uv run python scripts/measure_dropped.py <csv> [<csv> ...]
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

# 让脚本直接运行时也能 import 仓库根的模块（约定同 seed_admin.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.table.upload import importer
from app.table.upload.import_service import run_import
from app.table.upload.import_source import LocalFileSource

_timings: list[tuple[date, float, int]] = []
_original = importer._load_dropped


def _timed(session, site: str, period: str, report_date: date) -> int:
    started = time.perf_counter()
    rows = _original(session, site, period, report_date)
    _timings.append((report_date, time.perf_counter() - started, rows))
    return rows


importer._load_dropped = _timed


def main(paths: list[str]) -> None:
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        site, report_date = importer.parse_report_filename(name)
        period = importer.infer_period(name)
        started = time.perf_counter()
        batch = run_import(
            site=site, period=period, report_date=report_date,
            source=LocalFileSource(path), original_filename=name,
        )
        print(f"{report_date} {batch.status} 事实 {batch.written_rows} 行 "
              f"整体 {time.perf_counter() - started:.1f}s", flush=True)

    print("\n=== 掉榜集合算定耗时 ===", flush=True)
    for report_date, elapsed, rows in _timings:
        print(f"{report_date}  {elapsed:7.2f}s  {rows:>9,} 行", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
