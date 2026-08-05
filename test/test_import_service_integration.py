"""导入编排层的集成测试：批次记录、统计、物化视图刷新、原始文件留存、串行锁。

OSS-first 之后不再有导入后本地归档（文件入口即持久化于 OSS），故这里断言的是
"原始文件留在原处、不产生归档目录"。

与 importer 的测试不同，本层会 **提交事务**（真实写库），因此不能用回滚隔离。
每个测试用后 TRUNCATE 清理。只在 TEST_DATABASE_URL 时运行。

运行::

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_import_service_integration.py -v
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行编排层集成测试")

_META = "报告范围=US"
_HEADER = ",".join(["搜索频率排名", "搜索词"] + [f"col{i}" for i in range(19)])


def _row(rank, keyword, a1="B1", t1="T", b1="Brand", cat1="Electronics", cs1="10.0"):
    cells = [str(rank), keyword, b1, "", "", cat1, "", "", a1, t1, cs1, "",
             "", "", "", "", "", "", "", "", "2026-07-15"]
    buf = io.StringIO(); csv.writer(buf).writerow(cells)
    return buf.getvalue().rstrip("\r\n")


def _write_csv(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join([_META, _HEADER, *rows]) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture()
def factory():
    engine = create_engine(TEST_URL)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    yield sf
    # 清理：TRUNCATE 全部业务表 + 物化视图数据
    with engine.begin() as c:
        c.execute(text("TRUNCATE analysis.fact_daily, analysis.fact_weekly, "
                       "analysis.dim_keyword, analysis.dim_product, "
                       "analysis.site_data_stats, analysis.import_batch RESTART IDENTITY CASCADE"))
    engine.dispose()


def test_full_import_from_local_source_records_batch_and_stats(factory, tmp_path):
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day_2026_07_15.csv", [_row(1, "a"), _row(2, "b")])
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(path), original_filename="US_Day_2026_07_15.csv",
                       session_factory=factory)
    assert batch.status == "COMPLETED"
    assert batch.written_rows == 2

    with factory() as s:
        # 事实落库
        # 计数**必须带作用域**：keywordiq_test 是全局共享的一份，别的会话或一次
        # verify-import 留下的已提交数据会让裸 count(*) 数到不属于本测试的行。
        assert s.execute(text("SELECT count(*) FROM analysis.fact_daily "
                              "WHERE site='US' AND report_date='2026-07-15'")).scalar() == 2
        # 统计写入
        n = s.execute(text("SELECT row_count FROM analysis.site_data_stats "
                           "WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'")).scalar()
        assert n == 2
        # 批次记录可查
        assert s.execute(text("SELECT count(*) FROM analysis.import_batch "
                              "WHERE status='COMPLETED' AND site='US' "
                              "AND report_date='2026-07-15'")).scalar() == 1
        # 物化视图已刷新（有类目数据）
        cats = s.execute(text("SELECT count(*) FROM analysis.category_stats WHERE site='US'")).scalar()
        assert cats >= 1

    # OSS-first：不再做导入后本地归档，原始文件留在原处；本地来源无 object_key。
    assert Path(path).exists()
    assert not (tmp_path / "archive").exists()


def test_failed_import_preserves_file_and_marks_failed(factory, tmp_path):
    from app.table.upload.import_service import run_import

    # 结构不符（缺元数据行的关键字），触发失败
    from app.table.upload.import_source import LocalFileSource
    bad = tmp_path / "bad.csv"
    bad.write_text("not,a,valid,report\n", encoding="utf-8")
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(str(bad)), original_filename="bad.csv",
                       session_factory=factory)
    # 结构不符 → 失败，原始文件保留（不归档）供排查
    assert batch.status == "FAILED"
    assert batch.error_message
    assert Path(bad).exists()


def test_second_concurrent_import_is_rejected(factory, tmp_path):
    """持有锁时，第二个导入被拒（串行）。"""
    from app.table.upload.import_service import _IMPORT_LOCK_KEY, ImportRejected, run_import
    from app.table.upload.import_source import LocalFileSource
    # 手动占住锁，模拟"已有导入在跑"
    engine = create_engine(TEST_URL)
    holder = engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _IMPORT_LOCK_KEY})
    holder.commit()
    try:
        path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
        with pytest.raises(ImportRejected):
            run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(path), original_filename="US_Day.csv",
                       session_factory=factory)
        # 被拒时批次记录为 QUEUED
        with factory() as s:
            assert s.execute(text("SELECT status FROM analysis.import_batch")).scalar() == "QUEUED"
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _IMPORT_LOCK_KEY})
        holder.commit(); holder.close(); engine.dispose()
