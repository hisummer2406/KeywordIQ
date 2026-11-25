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


def _batch(
    s, period: str, d: date, key: str, *, status="COMPLETED", written=1, committed: str | None = None
) -> int:
    """``committed`` 是事实提交时刻（如 ``'2031-04-11 10:00+00'``）；None 表示迁移前批次。"""
    return s.execute(
        text("INSERT INTO analysis.import_batch"
             "(site,period_type,report_date,file_name,status,written_rows,object_key,facts_committed_at) "
             "VALUES ('US',:p,:d,:m,:st,:w,:k,CAST(:c AS timestamptz)) RETURNING id"),
        {"p": period, "d": d, "m": _MARK, "st": status, "w": written, "k": key, "c": committed},
    ).scalar_one()


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


def _april(result):
    return [(g.reason, g.batch_id) for g in result.blocked if g.month == date(2031, 4, 1)]


def test_committed_writer_decides_even_when_ids_are_out_of_order(session):
    """#209 P1：id 小的批次后提交事实（id 在上传时分配），它才是当前数据的写入者。
    它的原始文件缺失、id 更大的旧批次文件仍在时，整月必须保留。"""
    _daily(session, _BLOCKED, "apr")
    writer = _batch(session, "DAY", _BLOCKED, "k/writer", committed="2031-04-11 12:00+00")
    _batch(session, "DAY", _BLOCKED, "k/overwritten", committed="2031-04-11 09:00+00")
    session.commit()

    result = _run(session, _stat({"k/overwritten": 5}), dry_run=True)

    assert _april(result) == [("object_missing", writer)]


def test_confirmed_writer_alone_is_checked(session):
    """写入者已确认时，被覆盖的旧批次（含迁移前的）原始文件缺失不影响移出。"""
    _daily(session, _BLOCKED, "apr")
    _batch(session, "DAY", _BLOCKED, "k/legacy-gone")
    _batch(session, "DAY", _BLOCKED, "k/old-gone", committed="2031-04-11 09:00+00")
    _batch(session, "DAY", _BLOCKED, "k/writer", committed="2031-04-11 12:00+00")
    session.commit()

    result = _run(session, _stat({"k/writer": 5}), dry_run=True)

    assert _april(result) == []


def test_writer_whose_finish_failed_is_still_the_writer(session):
    """事实已提交、收尾失败的批次（FAILED）数据仍在库里，校验的是它的原始文件。"""
    _daily(session, _BLOCKED, "apr")
    _batch(session, "DAY", _BLOCKED, "k/completed-earlier", committed="2031-04-11 09:00+00")
    writer = _batch(
        session, "DAY", _BLOCKED, "k/finish-failed", status="FAILED", committed="2031-04-11 12:00+00"
    )
    session.commit()

    result = _run(session, _stat({"k/completed-earlier": 5}), dry_run=True)

    assert _april(result) == [("object_missing", writer)]


def test_legacy_periods_require_every_possible_writer(session):
    """迁移前没有提交记录：成功批次与写过行的批次都可能是写入者，全部通过才放行；
    事实事务里就失败的批次（未写行）不是候选。"""
    _daily(session, _BLOCKED, "apr")
    _batch(session, "DAY", _BLOCKED, "k/a")
    _batch(session, "DAY", _BLOCKED, "k/b")
    _batch(session, "DAY", _BLOCKED, "k/failed-before-commit", status="FAILED", written=0)
    session.commit()

    assert _april(_run(session, _stat({"k/a": 5, "k/b": 5}), dry_run=True)) == []

    finish_failed = _batch(session, "DAY", _BLOCKED, "k/finish-failed", status="FAILED", written=3)
    session.commit()

    result = _run(session, _stat({"k/a": 5, "k/b": 5}), dry_run=True)
    assert _april(result) == [("owner_unconfirmed:object_missing", finish_failed)]


def test_single_legacy_batch_keeps_plain_reason(session):
    _daily(session, _BLOCKED, "apr")
    only = _batch(session, "DAY", _BLOCKED, "k/only")
    session.commit()

    assert _april(_run(session, _stat({}), dry_run=True)) == [("object_missing", only)]


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

    # 再来一个迁移前批次：两者都可能是写入者，原因注明归属无法确认。
    _batch(session, "DAY", _BLOCKED, "")
    session.commit()
    result = _run(session, _stat({"k/a": 5}), dry_run=True)
    assert [g.reason for g in result.blocked if g.month == date(2031, 4, 1)] == [
        "owner_unconfirmed:empty_object_key"
    ]


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
