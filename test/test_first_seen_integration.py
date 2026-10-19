"""永久首次入榜记录（keyword-first-seen）的导入集成测试。

验证 ``importer._upsert_keywords`` 在真实 PostgreSQL 上的行为：基线期、已确认首次、乱序/补
历史向前修正、日周并列与细化、站点隔离、存量 UNKNOWN、保留清理后不重置，以及日/周视图的
命中口径（``analysis_crud.first_listed_*``）。

跑法同 ``test_importer_integration.py``（需 ``TEST_DATABASE_URL``，每个测试独占事务回滚）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from test import test_importer_integration as _base
from test.test_importer_integration import _import, _row, pytestmark  # noqa: F401

# pytest 从本模块命名空间解析 fixture；赋值而不是 `import session`，免得参数名触发 F811。
session = _base.session

D = date


def _kw(name: str) -> str:
    return _row(10, name, a1="B1", t1="T")


def _imp(session, tmp_path, tag, names, d, *, site="US", period="DAY"):
    return _import(
        session, tmp_path, f"{tag}.csv", [_kw(n) for n in names], site=site, period=period, d=d
    )


def _fs(session, name, site="US"):
    row = session.execute(
        text(
            "SELECT first_seen_date, first_seen_period_type, first_seen_basis "
            "FROM analysis.dim_keyword WHERE site=:s AND keyword=:k"
        ),
        {"s": site, "k": name},
    ).one()
    return tuple(row)


def test_site_first_import_is_baseline_not_first_listed(session, tmp_path):
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 7, 10))
    assert _fs(session, "x") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "y") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")


def test_later_period_new_words_are_confirmed(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "a"], D(2026, 7, 12))
    assert _fs(session, "a") == (D(2026, 7, 12), "DAY", "CONFIRMED")
    assert _fs(session, "x")[2] == "EARLIEST_OBSERVED"  # 存量不被升级


def test_reimport_same_period_is_stable(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "a"], D(2026, 7, 12))
    before = _fs(session, "a")
    for i in range(2):
        _imp(session, tmp_path, f"r{i}", ["x", "a"], D(2026, 7, 12))
    assert _fs(session, "a") == before
    assert session.execute(text("SELECT count(*) FROM analysis.dim_keyword")).scalar() == 2


def test_out_of_order_earlier_report_moves_first_seen_forward_in_time(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "a"], D(2026, 7, 13))
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 11))
    assert _fs(session, "a") == (D(2026, 7, 11), "DAY", "CONFIRMED")


def test_first_seen_never_moves_later(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 11))
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 15))
    assert _fs(session, "a")[0] == D(2026, 7, 11)


def test_backfilling_before_site_start_is_baseline(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["y"], D(2026, 7, 5))
    assert _fs(session, "y") == (D(2026, 7, 5), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "x") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")


def test_confirmed_is_never_downgraded_by_earlier_baseline(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 12))
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 5))
    assert _fs(session, "a") == (D(2026, 7, 5), "DAY", "CONFIRMED")


def test_same_date_day_beats_week_in_either_order(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    # 周报先、日报后
    _imp(session, tmp_path, "w1", ["p"], D(2026, 7, 12), period="WEEK")
    _imp(session, tmp_path, "d1", ["p"], D(2026, 7, 12))
    # 日报先、周报后
    _imp(session, tmp_path, "d2", ["q"], D(2026, 7, 13))
    _imp(session, tmp_path, "w2", ["q"], D(2026, 7, 13), period="WEEK")
    assert _fs(session, "p") == (D(2026, 7, 12), "DAY", "CONFIRMED")
    assert _fs(session, "q") == (D(2026, 7, 13), "DAY", "CONFIRMED")


def test_late_daily_report_refines_earlier_weekly_first_seen(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 1))
    _imp(session, tmp_path, "w", ["c"], D(2026, 7, 18), period="WEEK")
    assert _fs(session, "c") == (D(2026, 7, 18), "WEEK", "CONFIRMED")
    _imp(session, tmp_path, "d", ["c"], D(2026, 7, 15))
    assert _fs(session, "c") == (D(2026, 7, 15), "DAY", "CONFIRMED")


def test_sites_are_independent(session, tmp_path):
    _imp(session, tmp_path, "us1", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "gb1", ["x"], D(2026, 7, 10), site="GB")
    _imp(session, tmp_path, "us2", ["x", "z"], D(2026, 7, 11))
    _imp(session, tmp_path, "gb2", ["x", "z"], D(2026, 7, 12), site="GB")
    assert _fs(session, "x", "GB") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "z", "US") == (D(2026, 7, 11), "DAY", "CONFIRMED")
    assert _fs(session, "z", "GB") == (D(2026, 7, 12), "DAY", "CONFIRMED")


def test_legacy_unknown_word_becomes_earliest_observed_never_confirmed(session, tmp_path):
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'old')"))
    assert _fs(session, "old") == (None, None, "UNKNOWN")
    _imp(session, tmp_path, "a", ["old", "fresh"], D(2026, 7, 10))
    # 站点此前没有任何首次记录 → 本期是基线期：新词也只是最早可观测
    assert _fs(session, "old") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "fresh") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")
    _imp(session, tmp_path, "b", ["old", "newer"], D(2026, 7, 11))
    assert _fs(session, "newer")[2] == "CONFIRMED"
    assert _fs(session, "old")[2] == "EARLIEST_OBSERVED"


def test_first_seen_survives_fact_cleanup(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 11))
    session.execute(text("DELETE FROM analysis.fact_daily"))  # 模拟保留清理移出分区
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 20))
    assert _fs(session, "a") == (D(2026, 7, 11), "DAY", "CONFIRMED")


def test_rolled_back_import_leaves_no_first_seen(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    nested = session.begin_nested()
    _imp(session, tmp_path, "b", ["ghost"], D(2026, 7, 12))
    nested.rollback()
    assert session.execute(
        text("SELECT count(*) FROM analysis.dim_keyword WHERE keyword='ghost'")
    ).scalar() == 0


def _count(session, view, on, site="US"):
    from app.table.analysis import analysis_crud as crud

    return session.execute(crud.first_listed_count_stmt(site, view, on)).scalar_one()


def test_day_and_week_views_use_the_fixed_mapping(session, tmp_path):
    """design D4 的例子：A 日报 7/10，B 周报 7/13（覆盖 7/7–7/13），C 日报 7/15。"""
    _imp(session, tmp_path, "base_d", ["base"], D(2026, 7, 1))
    _imp(session, tmp_path, "d10", ["base", "A"], D(2026, 7, 10))
    _imp(session, tmp_path, "w13", ["base", "A", "B"], D(2026, 7, 13), period="WEEK")
    _imp(session, tmp_path, "d15", ["base", "C"], D(2026, 7, 15))
    _imp(session, tmp_path, "w20", ["base", "C"], D(2026, 7, 20), period="WEEK")

    assert _count(session, "DAY", D(2026, 7, 10)) == 1  # A
    assert _count(session, "DAY", D(2026, 7, 15)) == 1  # C
    assert _count(session, "WEEK", D(2026, 7, 13)) == 2  # A + B，同一词只计一次
    assert _count(session, "WEEK", D(2026, 7, 20)) == 1  # C，跨周边界落在后一周
    # 基线期的词永远不算
    assert _count(session, "DAY", D(2026, 7, 1)) == 0

    # 周报来源的 B 不进任何一天的日视图
    _imp(session, tmp_path, "d13", ["base"], D(2026, 7, 13))
    assert _count(session, "DAY", D(2026, 7, 13)) == 0

    # 之后补导入含 B 的日报 7/9：B 细化为 (7/9, DAY)；周视图计数不变
    _imp(session, tmp_path, "d9", ["base", "B"], D(2026, 7, 9))
    assert _count(session, "DAY", D(2026, 7, 9)) == 1
    assert _count(session, "WEEK", D(2026, 7, 13)) == 2


def test_unknown_and_earliest_observed_are_never_counted(session, tmp_path):
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'old')"))
    _imp(session, tmp_path, "a", ["old", "x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["old", "x", "n"], D(2026, 7, 11))
    assert _count(session, "DAY", D(2026, 7, 10)) == 0
    assert _count(session, "DAY", D(2026, 7, 11)) == 1  # 只有 n


def test_filter_total_equals_summary_count_and_rows_carry_flags(session, tmp_path):
    """筛选结果总数与数量接口用同一口径；行投影带 first_listed / 首次日期 / 依据。"""
    from app.table.analysis import analysis_crud as crud
    from app.table.search.search_schemas import AnalysisSearchRequest

    _imp(session, tmp_path, "d1", ["base"], D(2026, 7, 10))
    _imp(session, tmp_path, "d2", ["base", "n1", "n2"], D(2026, 7, 11))
    req = AnalysisSearchRequest(site="US", view="DAY", date=D(2026, 7, 11), first_listed=True)

    total = session.execute(crud.count_filtered_stmt("US", "DAY", D(2026, 7, 11), req, 1)).scalar_one()
    assert total == _count(session, "DAY", D(2026, 7, 11)) == 2

    page = crud.search_stmt("US", "DAY", D(2026, 7, 11), req, user_id=1)
    rows = session.execute(page).all()
    assert {r.keyword for r in rows} == {"n1", "n2"}
    assert all(r.first_listed and r.first_seen_basis == "CONFIRMED" for r in rows)

    # 不带筛选时全部行都在，只有首次入榜的两行带标记
    all_rows = session.execute(
        crud.search_stmt("US", "DAY", D(2026, 7, 11),
                         AnalysisSearchRequest(site="US", view="DAY", date=D(2026, 7, 11)), user_id=1)
    ).all()
    flags = {r.keyword: (r.first_listed, r.first_seen_basis) for r in all_rows}
    assert flags["base"] == (False, "EARLIEST_OBSERVED")
    assert flags["n1"] == (True, "CONFIRMED")


def test_confirmed_since_is_the_first_judgeable_period(session, tmp_path):
    """首次入榜统计的起点 = 站点最早的已确认首次期次；之前（含回填出来的历史）无法判断。"""
    from app.table.analysis import analysis_crud as crud

    def since():
        return session.execute(crud.confirmed_since_stmt("US")).scalar()

    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "y"], D(2026, 7, 11))  # 7/10 是基线，7/11 起才有已确认
    assert since() == D(2026, 7, 11)
    _imp(session, tmp_path, "c", ["x", "y", "z"], D(2026, 7, 12))
    assert since() == D(2026, 7, 11)  # 7/10 无法判断，7/11 起可判断
