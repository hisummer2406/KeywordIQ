"""归档闸门的真实 PostgreSQL 回归：只有原始归档完整的月份才能被移出。

run_retention 真实运行会提交，且要在共享的 keywordiq_test 上持有导入咨询锁，因此这里
用独立的远期年份（2031）建数据，测试结束显式清理，不依赖事务回滚。
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.table.analysis import partitioning, retention_service
from app.table.upload.oss_client import OSSObjectNotFound

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

_TODAY = date(2031, 8, 15)  # 保留 3 个月 → cutoff 2031-06-01
_BLOCKED = date(2031, 4, 10)
_PASSING = date(2031, 5, 10)
_MARK = "gate-test"


@pytest.fixture()
def session():
    engine = create_engine(TEST_URL)
    s = Session(bind=engine)
    try:
        yield s
    finally:
        s.rollback()
        for table in ("fact_daily", "fact_weekly", "fact_dropped"):
            for part in partitioning.list_month_partitions(s, table):
                if part.start.year == 2031:
                    partitioning.drop_month_partition(s, table, part.name)
        s.execute(text("DELETE FROM analysis.import_batch WHERE file_name = :m"), {"m": _MARK})
        s.execute(text("DELETE FROM analysis.dim_keyword WHERE keyword LIKE :m"), {"m": f"{_MARK}%"})
        s.commit()
        s.close()
        engine.dispose()


def _keyword(s, name: str) -> int:
    return s.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES ('US',:k) RETURNING keyword_id"),
        {"k": f"{_MARK}-{name}"},
    ).scalar()


def _daily(s, d: date, name: str) -> None:
    partitioning.ensure_fact_partitions(s, "fact_daily", "US", d)
    s.execute(
        text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
             "VALUES ('US',:d,:k,1)"),
        {"d": d, "k": _keyword(s, name)},
    )


def _weekly(s, d: date, name: str) -> None:
    partitioning.ensure_fact_partitions(s, "fact_weekly", "US", d)
    s.execute(
        text("INSERT INTO analysis.fact_weekly(site,report_date_week,keyword_id,current_ranking) "
             "VALUES ('US',:d,:k,1)"),
        {"d": d, "k": _keyword(s, name)},
    )


def _dropped(s, d: date, name: str) -> None:
    partitioning.ensure_fact_partitions(s, "fact_dropped", "US", d)
    s.execute(
        text("INSERT INTO analysis.fact_dropped"
             "(site,period_type,anchor_date,keyword_id,last_seen_date,last_ranking) "
             "VALUES ('US','DAY',:d,:k,:d,1)"),
        {"d": d, "k": _keyword(s, name)},
    )


def _batch(s, period: str, d: date, key: str, *, status="COMPLETED", written=1) -> None:
    s.execute(
        text("INSERT INTO analysis.import_batch"
             "(site,period_type,report_date,file_name,status,written_rows,object_key) "
             "VALUES ('US',:p,:d,:m,:st,:w,:k)"),
        {"p": period, "d": d, "m": _MARK, "st": status, "w": written, "k": key},
    )


def _partitions(s, table: str) -> set[str]:
    return {p.name for p in partitioning.list_month_partitions(s, table)}


def _stat(sizes: dict[str, int]):
    def stat(key: str) -> int:
        if key not in sizes:
            raise OSSObjectNotFound(log_detail=key)
        return sizes[key]

    return stat


def _run(s, stat, *, dry_run=False):
    return retention_service.run_retention(
        s, today=_TODAY, retention_months=3, dry_run=dry_run, archive_stat=stat
    )


def _seed_two_months(s) -> None:
    _daily(s, _BLOCKED, "apr")
    _weekly(s, _BLOCKED, "apr-w")
    _dropped(s, _BLOCKED, "apr-d")
    _batch(s, "DAY", _BLOCKED, "k/apr-day")
    _batch(s, "WEEK", _BLOCKED, "k/apr-week")
    _daily(s, _PASSING, "may")
    _batch(s, "DAY", _PASSING, "k/may-day")
    s.commit()


def test_missing_object_keeps_whole_month_and_other_month_is_dropped(session):
    _seed_two_months(session)
    sizes = {"k/apr-day": 5, "k/may-day": 5}  # 4 月周报对象缺失

    result = _run(session, _stat(sizes))

    assert any(
        g.report_date == _BLOCKED and g.period_type == "WEEK" and g.reason == "object_missing"
        for g in result.blocked
    )
    assert "fact_daily_2031_04" in _partitions(session, "fact_daily")
    assert "fact_weekly_2031_04" in _partitions(session, "fact_weekly")
    assert "fact_dropped_2031_04" in _partitions(session, "fact_dropped")
    assert "fact_daily_2031_05" not in _partitions(session, "fact_daily")


def test_empty_object_on_weekly_blocks_month(session):
    _seed_two_months(session)

    result = _run(session, _stat({"k/apr-day": 5, "k/apr-week": 0, "k/may-day": 5}), dry_run=True)

    assert {g.reason for g in result.blocked if g.month == date(2031, 4, 1)} == {"object_empty"}


def test_dry_run_reports_without_writing(session):
    _seed_two_months(session)

    result = _run(session, _stat({"k/may-day": 5}), dry_run=True)

    assert date(2031, 4, 1) in result.blocked_months
    assert "fact_daily_2031_04" not in result.dropped["fact_daily"]
    assert "fact_daily_2031_05" in result.dropped["fact_daily"]
    assert "fact_daily_2031_05" in _partitions(session, "fact_daily")


def test_latest_completed_batch_decides(session):
    _daily(session, _BLOCKED, "apr")
    _batch(session, "DAY", _BLOCKED, "k/old")
    _batch(session, "DAY", _BLOCKED, "k/new")
    _batch(session, "DAY", _BLOCKED, "k/failed-later", status="FAILED")
    session.commit()

    result = _run(session, _stat({"k/old": 5}), dry_run=True)

    assert [g.reason for g in result.blocked if g.month == date(2031, 4, 1)] == ["object_missing"]


def test_no_batch_blocks_and_oss_error_fails_closed(session):
    _daily(session, _BLOCKED, "apr")
    session.commit()
    no_batch = _run(session, _stat({}), dry_run=True)
    assert [g.reason for g in no_batch.blocked if g.month == date(2031, 4, 1)] == ["no_batch"]

    _batch(session, "DAY", _BLOCKED, "k/a")
    session.commit()

    def boom(_key: str) -> int:
        raise ConnectionError("oss down")

    errored = _run(session, boom, dry_run=False)
    assert [g.reason for g in errored.blocked if g.month == date(2031, 4, 1)] == ["oss_unavailable"]
    assert "fact_daily_2031_04" in _partitions(session, "fact_daily")


def test_zero_rows_and_empty_key_block(session):
    _daily(session, _BLOCKED, "apr")
    _batch(session, "DAY", _BLOCKED, "k/a", written=0)
    session.commit()
    result = _run(session, _stat({"k/a": 5}), dry_run=True)
    assert [g.reason for g in result.blocked if g.month == date(2031, 4, 1)] == ["no_rows"]

    _batch(session, "DAY", _BLOCKED, "")
    session.commit()
    result = _run(session, _stat({"k/a": 5}), dry_run=True)
    assert [g.reason for g in result.blocked if g.month == date(2031, 4, 1)] == ["empty_object_key"]


def test_export_dir_handles_site_subpartitioned_month(session, tmp_path):
    _daily(session, _PASSING, "may")
    _batch(session, "DAY", _PASSING, "k/may-day")
    session.commit()

    result = retention_service.run_retention(
        session, today=_TODAY, retention_months=3, export_dir=str(tmp_path),
        archive_stat=_stat({"k/may-day": 5}),
    )

    exported = tmp_path / "fact_daily_2031_05.csv"
    assert str(exported) in result.exported["fact_daily"]
    lines = exported.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "site" in lines[0].split(",")
    assert "2031-05-10" in lines[1]
