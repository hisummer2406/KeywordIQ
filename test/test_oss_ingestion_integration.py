"""OSS-first 导入的集成测试：以 OSS 对象为源，流式 COPY 落库。

分两层：

- **层一（有 TEST_DATABASE_URL 即运行）**：用 `OSSObjectSource` 从一个真实字节流（内存）
  流式喂 `COPY` 到真实 Postgres——验证 OSS 形态的流式装载 + 事实落库 + 批次记录持久化
  object key。这正是 5.4 关心的"流式 COPY → 事实落库"的 DB 侧，且无需云账号即可跑。
- **层二（另需 TEST_OSS_* 才运行）**：真实往测试 bucket `put_object`，再经
  `oss_client.object_stream` 读回导入——覆盖"直传对象 → 真实 OSS 读取 → 落库"的完整回路。

运行::

    # 层一
    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_oss_ingestion_integration.py -v
    # 追加层二（真实 OSS）
    TEST_OSS_ENDPOINT=... TEST_OSS_BUCKET=... TEST_OSS_ACCESS_KEY_ID=... \\
    TEST_OSS_ACCESS_KEY_SECRET=... TEST_DATABASE_URL=... \\
        uv run pytest test/test_oss_ingestion_integration.py -v
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.table.upload import oss_client
from app.table.upload.import_service import run_import
from app.table.upload.import_source import OSSObjectSource

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行 OSS 导入集成测试")

_META = "报告范围=US"
_HEADER = ",".join(["搜索频率排名", "搜索词"] + [f"col{i}" for i in range(19)])
_RDATE = date(2026, 7, 15)
_OBJECT_KEY = f"imports/US/DAY/{_RDATE}/deadbeef_US_x_Day_2026_07_15.csv"

# 真实 OSS 回路所需的环境（缺任一则层二跳过）。
_OSS_ENV = ("TEST_OSS_ENDPOINT", "TEST_OSS_BUCKET", "TEST_OSS_ACCESS_KEY_ID", "TEST_OSS_ACCESS_KEY_SECRET")
_has_oss = all(os.environ.get(k) for k in _OSS_ENV)


def _row(rank, keyword, a1="B1", t1="T", b1="Brand", cat1="Electronics", cs1="10.0"):
    cells = [str(rank), keyword, b1, "", "", cat1, "", "", a1, t1, cs1, "",
             "", "", "", "", "", "", "", "", str(_RDATE)]
    buf = io.StringIO(); csv.writer(buf).writerow(cells)
    return buf.getvalue().rstrip("\r\n")


def _csv_bytes(rows) -> bytes:
    # 带 BOM，忠实还原真实报表首行（importer 按 utf-8-sig 解析）。
    return ("\n".join([_META, _HEADER, *rows]) + "\n").encode("utf-8-sig")


@pytest.fixture()
def factory():
    # NullPool：每个连接用完即关，不在池中滞留。避免测试间快速新建/dispose 引擎时，
    # 上一个测试的后端连接尚未被 PostgreSQL 回收就与本测试的 pg_try_advisory_lock 抢锁
    # （纯测试隔离的时序问题；生产用单一长生命周期 SessionFactory，导入本就串行，不受影响）。
    engine = create_engine(TEST_URL, poolclass=NullPool)
    exists = engine.connect().execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar()
    if exists is None:
        engine.dispose()
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    yield sf
    with engine.begin() as c:
        c.execute(text("TRUNCATE analysis.fact_daily, analysis.fact_weekly, "
                       "analysis.dim_keyword, analysis.dim_product, "
                       "analysis.site_data_stats, analysis.import_batch RESTART IDENTITY CASCADE"))
    engine.dispose()


# --------------------------------------------------------------------------
# 层一：OSS 形态的流式源 → COPY → 真实 Postgres
# --------------------------------------------------------------------------


def test_oss_source_streams_into_facts_and_records_object_key(factory):
    payload = _csv_bytes([_row(1, "obsession"), _row(2, "ventilator")])
    # opener 返回一个真实字节流，走 OSSObjectSource 的流式解码路径。
    source = OSSObjectSource(lambda k: io.BytesIO(payload), _OBJECT_KEY)

    batch = run_import(
        site="US", period="DAY", report_date=_RDATE,
        source=source, original_filename="US_x_Day_2026_07_15.csv",
        object_key=_OBJECT_KEY, session_factory=factory,
    )

    assert batch.status == "COMPLETED"
    assert batch.written_rows == 2
    # 批次记录持久化 object key，供溯源与重导。
    assert batch.object_key == _OBJECT_KEY

    with factory() as s:
        assert s.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 2
        n = s.execute(text("SELECT row_count FROM analysis.site_data_stats "
                           "WHERE site='US' AND period_type='DAY' AND report_date=:d"),
                      {"d": _RDATE}).scalar()
        assert n == 2
        # 库里的批次记录也带 object key。
        persisted = s.execute(text("SELECT object_key FROM analysis.import_batch "
                                   "WHERE status='COMPLETED'")).scalar()
        assert persisted == _OBJECT_KEY


def test_reimport_same_slice_overwrites_via_oss_source(factory):
    """同期重复导入=覆盖：先 1 行，再 2 行，最终应为 2 行（不叠加）。"""
    first = OSSObjectSource(lambda k: io.BytesIO(_csv_bytes([_row(1, "solo")])), _OBJECT_KEY)
    run_import(site="US", period="DAY", report_date=_RDATE, source=first,
               original_filename="a.csv", object_key=_OBJECT_KEY, session_factory=factory)

    again = OSSObjectSource(lambda k: io.BytesIO(_csv_bytes([_row(1, "a"), _row(2, "b")])), _OBJECT_KEY)
    run_import(site="US", period="DAY", report_date=_RDATE, source=again,
               original_filename="a.csv", object_key=_OBJECT_KEY, session_factory=factory)

    with factory() as s:
        assert s.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 2


# --------------------------------------------------------------------------
# 层二：真实 OSS 回路（put_object → object_stream → 落库）
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _has_oss, reason="设置 TEST_OSS_* 才运行真实 OSS 回路")
def test_real_oss_round_trip(factory):
    import oss2

    auth = oss2.Auth(os.environ["TEST_OSS_ACCESS_KEY_ID"], os.environ["TEST_OSS_ACCESS_KEY_SECRET"])
    bucket = oss2.Bucket(auth, os.environ["TEST_OSS_ENDPOINT"], os.environ["TEST_OSS_BUCKET"])

    key = f"kiq-test/{_OBJECT_KEY}"
    bucket.put_object(key, _csv_bytes([_row(1, "obsession"), _row(2, "ventilator")]))
    try:
        # 经真实 OSS 读回：opener 用 oss_client.object_stream + 注入的真实 bucket。
        source = OSSObjectSource(lambda k: oss_client.object_stream(k, bucket=bucket), key)
        assert oss_client.object_exists(key, bucket=bucket) is True

        batch = run_import(
            site="US", period="DAY", report_date=_RDATE,
            source=source, original_filename="US_x_Day_2026_07_15.csv",
            object_key=key, session_factory=factory,
        )
        assert batch.status == "COMPLETED"
        assert batch.written_rows == 2
        with factory() as s:
            assert s.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 2
    finally:
        bucket.delete_object(key)
