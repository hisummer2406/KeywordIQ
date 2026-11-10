"""周期首次证据（#178，keyword-first-seen「关键词按周期保存独立的永久首次证据」）的导入集成测试。

验证 ``importer._upsert_keywords`` 在真实 PostgreSQL 上维护 ``day_first_*`` / ``week_first_*``：
新词本周期导入创建、另一周期已确认未出现；先周后日（#178 样例形态）；存量词不升级；乱序前移；
全排列顺序无关；回滚不留痕；跨站点独立；共享首次记录不受影响。

跑法同 ``test_importer_integration.py``（需 ``TEST_DATABASE_URL``，每个测试独占事务回滚）。
"""

from __future__ import annotations

import itertools
from datetime import date

from sqlalchemy import text

from test import test_importer_integration as _base
from test.test_importer_integration import _import, _row, pytestmark  # noqa: F401

session = _base.session

D = date


def _imp(session, tmp_path, tag, names, d, *, site="US", period="DAY"):
    rows = [_row(10, n, a1="B1", t1="T") for n in names]
    return _import(session, tmp_path, f"{tag}.csv", rows, site=site, period=period, d=d)


def _pf(session, name, site="US"):
    """(日榜日期, 日榜等级, 周榜日期, 周榜等级)。"""
    row = session.execute(
        text(
            "SELECT day_first_date, day_first_basis, week_first_date, week_first_basis "
            "FROM analysis.dim_keyword WHERE site=:s AND keyword=:k"
        ),
        {"s": site, "k": name},
    ).one()
    return tuple(row)


def _shared(session, name, site="US"):
    row = session.execute(
        text(
            "SELECT first_seen_date, first_seen_period_type, first_seen_basis "
            "FROM analysis.dim_keyword WHERE site=:s AND keyword=:k"
        ),
        {"s": site, "k": name},
    ).one()
    return tuple(row)


def test_new_word_imported_in_its_period_absent_in_other(session, tmp_path):
    _imp(session, tmp_path, "d", ["x"], D(2026, 9, 14))
    _imp(session, tmp_path, "w", ["y"], D(2026, 9, 12), period="WEEK")
    assert _pf(session, "x") == (D(2026, 9, 14), "IMPORTED", None, "ABSENT")
    assert _pf(session, "y") == (None, "ABSENT", D(2026, 9, 12), "IMPORTED")


def test_week_first_then_day_first_is_day_imported(session, tmp_path):
    """#178 样例形态：9/26 周报新建，10/31 才上日报——日榜证据是 10/31 导入创建，共享记录不变。"""
    _imp(session, tmp_path, "w", ["crinkle newspaper dog toy"], D(2026, 9, 26), period="WEEK")
    _imp(session, tmp_path, "d", ["crinkle newspaper dog toy"], D(2026, 10, 31))
    assert _pf(session, "crinkle newspaper dog toy") == (
        D(2026, 10, 31), "IMPORTED", D(2026, 9, 26), "IMPORTED"
    )
    assert _shared(session, "crinkle newspaper dog toy") == (D(2026, 9, 26), "WEEK", "IMPORTED")


def test_legacy_unknown_becomes_earliest_observed_per_period(session, tmp_path):
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'old')"))
    assert _pf(session, "old") == (None, "UNKNOWN", None, "UNKNOWN")
    _imp(session, tmp_path, "d", ["old"], D(2026, 10, 1))
    assert _pf(session, "old") == (D(2026, 10, 1), "EARLIEST_OBSERVED", None, "UNKNOWN")
    _imp(session, tmp_path, "w", ["old"], D(2026, 10, 3), period="WEEK")
    assert _pf(session, "old") == (
        D(2026, 10, 1), "EARLIEST_OBSERVED", D(2026, 10, 3), "EARLIEST_OBSERVED"
    )


def test_earlier_report_moves_period_date_forward_keeps_basis(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 10, 31))
    _imp(session, tmp_path, "w", ["x"], D(2026, 10, 31), period="WEEK")
    _imp(session, tmp_path, "b", ["x"], D(2026, 10, 20))
    assert _pf(session, "x") == (D(2026, 10, 20), "IMPORTED", D(2026, 10, 31), "IMPORTED")
    _imp(session, tmp_path, "c", ["x"], D(2026, 11, 2))  # 更晚不写
    assert _pf(session, "x")[0] == D(2026, 10, 20)


def test_reimport_is_zero_update(session, tmp_path):
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 10, 1))
    # 任何 UPDATE 都会写新元组版本、ctid 随之改变；ctid 不变即零更新（相等不写的纪律）。
    q = text("SELECT ctid::text FROM analysis.dim_keyword WHERE site='US' ORDER BY keyword")
    before = session.execute(q).scalars().all()
    _imp(session, tmp_path, "a2", ["x", "y"], D(2026, 10, 1))
    assert session.execute(q).scalars().all() == before


def test_rolled_back_import_leaves_no_period_evidence(session, tmp_path):
    _imp(session, tmp_path, "w", ["x"], D(2026, 10, 3), period="WEEK")
    nested = session.begin_nested()
    _imp(session, tmp_path, "d", ["x"], D(2026, 10, 5))
    nested.rollback()
    assert _pf(session, "x") == (None, "ABSENT", D(2026, 10, 3), "IMPORTED")


def test_sites_are_independent(session, tmp_path):
    _imp(session, tmp_path, "us", ["x"], D(2026, 10, 3), period="WEEK")
    _imp(session, tmp_path, "gb", ["x"], D(2026, 10, 1), site="GB")
    assert _pf(session, "x", "US") == (None, "ABSENT", D(2026, 10, 3), "IMPORTED")
    assert _pf(session, "x", "GB") == (D(2026, 10, 1), "IMPORTED", None, "ABSENT")


_REPORTS = [
    ("d12", ["a", "b"], D(2026, 7, 12), "DAY"),
    ("d14", ["a", "c"], D(2026, 7, 14), "DAY"),
    ("w18", ["a", "b", "d"], D(2026, 7, 18), "WEEK"),
    ("d19", ["d", "e"], D(2026, 7, 19), "DAY"),
]


def _snapshot(session):
    return [tuple(r) for r in session.execute(text(
        "SELECT keyword, first_seen_date, first_seen_period_type, first_seen_basis,"
        " day_first_date, day_first_basis, week_first_date, week_first_basis"
        " FROM analysis.dim_keyword WHERE site='US' ORDER BY keyword"
    )).all()]


def _run_in_savepoint(session, tmp_path, order, *, repeat_at=None):
    nested = session.begin_nested()
    try:
        for i, idx in enumerate(order):
            tag, names, d, period = _REPORTS[idx]
            _imp(session, tmp_path, f"{tag}_{i}", names, d, period=period)
            if repeat_at == i:
                _imp(session, tmp_path, f"{tag}_rep{i}", names, d, period=period)
        return _snapshot(session)
    finally:
        nested.rollback()


def test_import_order_does_not_change_period_evidence(session, tmp_path):
    expected = _run_in_savepoint(session, tmp_path, (0, 1, 2, 3))
    rows = {r[0]: r[4:] for r in expected}
    assert rows["a"] == (D(2026, 7, 12), "IMPORTED", D(2026, 7, 18), "IMPORTED")
    assert rows["c"] == (D(2026, 7, 14), "IMPORTED", None, "ABSENT")
    # d 先在周报出现，之后才上日报：日榜证据是 7/19 导入创建
    assert rows["d"] == (D(2026, 7, 19), "IMPORTED", D(2026, 7, 18), "IMPORTED")
    assert rows["e"] == (D(2026, 7, 19), "IMPORTED", None, "ABSENT")

    for perm in itertools.permutations(range(len(_REPORTS))):
        assert _run_in_savepoint(session, tmp_path, perm) == expected, f"导入顺序 {perm}"
        got = _run_in_savepoint(session, tmp_path, perm, repeat_at=1)
        assert got == expected, f"导入顺序 {perm}，第 1 步后重复导入"


# ---- 查询口径（keyword-first-seen「首次进入当前榜单按周期独立派生」、site-scoped-query）----


def _count(session, view, on, mode="PERIOD", site="US"):
    from app.table.analysis import analysis_crud as crud

    return session.execute(crud.first_listed_count_stmt(site, view, on, mode)).scalar_one()


def _seed_178(session, tmp_path):
    """#178 形态：周报 9/12、9/19；日报 9/14（日榜起点）、10/30、10/31。

    - candle：9/12 周报（周榜起点当期）→ 10/31 才上日报
    - toy：9/19 周报新建 → 10/31 才上日报
    - fresh：10/31 日报新建（共享首次也是 10/31）
    - wk：9/19 周报新建，从未上日报
    """
    _imp(session, tmp_path, "w12", ["base", "candle"], D(2026, 9, 12), period="WEEK")
    _imp(session, tmp_path, "d14", ["base"], D(2026, 9, 14))
    _imp(session, tmp_path, "w19", ["base", "toy", "wk"], D(2026, 9, 19), period="WEEK")
    _imp(session, tmp_path, "d30", ["base"], D(2026, 10, 30))
    _imp(session, tmp_path, "d31", ["base", "candle", "toy", "fresh"], D(2026, 10, 31))


def test_period_mode_counts_first_day_listing_monitor_does_not(session, tmp_path):
    _seed_178(session, tmp_path)
    # 首次进入日榜：candle、toy、fresh（三者日榜证据都是 10/31 导入创建，且 10/31 > B_DAY=9/14）
    assert _count(session, "DAY", D(2026, 10, 31)) == 3
    # 首次进入监测：只有 fresh（candle、toy 早在周报出现过）
    assert _count(session, "DAY", D(2026, 10, 31), mode="MONITOR") == 1
    # 周榜首次不做日周映射：9/19 周榜首次是 toy、wk；9/12 是周榜起点不可判断（命中 0）
    assert _count(session, "WEEK", D(2026, 9, 19)) == 2
    assert _count(session, "WEEK", D(2026, 9, 12)) == 0
    # 日榜起点当期不命中
    assert _count(session, "DAY", D(2026, 9, 14)) == 0


def test_period_filter_total_equals_count_and_rows_carry_period_fields(session, tmp_path):
    from app.table.analysis import analysis_crud as crud
    from app.table.search.search_schemas import AnalysisSearchRequest

    _seed_178(session, tmp_path)
    on = D(2026, 10, 31)
    req = AnalysisSearchRequest(site="US", view="DAY", date=on, first_listing="PERIOD")
    total = session.execute(crud.count_filtered_stmt("US", "DAY", on, req)).scalar_one()
    assert total == _count(session, "DAY", on) == 3
    rows = session.execute(crud.search_stmt("US", "DAY", on, req)).all()
    assert {r.keyword for r in rows} == {"candle", "toy", "fresh"}
    assert all(r.period_first_listed and r.period_first_date == on for r in rows)
    flags = {r.keyword: (r.period_first_listed, r.first_listed) for r in rows}
    assert flags["toy"] == (True, False)  # 日榜首次，不是监测首次
    assert flags["fresh"] == (True, True)

    # 不筛时 base 行带日榜证据但不命中（日榜起点当期出现过）
    all_rows = session.execute(crud.search_stmt(
        "US", "DAY", on, AnalysisSearchRequest(site="US", view="DAY", date=on)
    )).all()
    base = next(r for r in all_rows if r.keyword == "base")
    assert (base.period_first_listed, base.period_first_date, base.period_first_basis) == (
        False, D(2026, 9, 14), "IMPORTED"
    )


def test_period_and_legacy_flag_are_not_combined(session, tmp_path):
    """旧 first_listed=true 走共享口径；与 PERIOD 同传在请求模型 422（见 test_result_key）。"""
    from app.table.analysis import analysis_crud as crud
    from app.table.search.search_schemas import AnalysisSearchRequest

    _seed_178(session, tmp_path)
    on = D(2026, 10, 31)
    legacy = AnalysisSearchRequest(site="US", view="DAY", date=on, first_listed=True)
    assert session.execute(crud.count_filtered_stmt("US", "DAY", on, legacy)).scalar_one() == 1


def test_previous_period_missing(session, tmp_path):
    from app.table.analysis import analysis_crud as crud

    # import_report 不写批次；缺期判定读 import_batch，这里按已导入期次登记已完成批次。
    for d, period in ((D(2026, 9, 12), "WEEK"), (D(2026, 9, 14), "DAY"), (D(2026, 9, 19), "WEEK"),
                      (D(2026, 10, 30), "DAY"), (D(2026, 10, 31), "DAY")):
        session.execute(text(
            "INSERT INTO analysis.import_batch (site, period_type, report_date, status)"
            " VALUES ('US', :p, :d, 'COMPLETED')"
        ), {"p": period, "d": d})
    # 失败批次不算已导入
    session.execute(text(
        "INSERT INTO analysis.import_batch (site, period_type, report_date, status)"
        " VALUES ('US', 'DAY', '2026-10-29', 'FAILED')"
    ))

    def missing(view, on):
        return session.execute(crud.previous_period_missing_stmt("US", view, on)).scalar()

    assert missing("DAY", D(2026, 10, 31)) is False  # 10/30 已导入
    assert missing("DAY", D(2026, 10, 30)) is True   # 10/29 只有失败批次
    assert missing("WEEK", D(2026, 9, 19)) is False  # 9/12 已导入
