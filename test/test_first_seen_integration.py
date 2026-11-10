"""永久首次入榜记录（keyword-first-seen）的导入集成测试。

验证 ``importer._upsert_keywords`` 在真实 PostgreSQL 上的行为：导入创建、乱序/补
历史向前修正、顺序无关、日周并列与细化、站点隔离、存量 UNKNOWN、保留清理后不重置，以及日/周视图的
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


def test_new_words_are_always_imported(session, tmp_path):
    """导入只记证据：新词一律 IMPORTED，不在导入时判「是不是首次」（那是查询时的派生）。"""
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 7, 10))
    assert _fs(session, "x") == (D(2026, 7, 10), "DAY", "IMPORTED")
    _imp(session, tmp_path, "b", ["x", "a"], D(2026, 7, 12))
    assert _fs(session, "a") == (D(2026, 7, 12), "DAY", "IMPORTED")
    assert _fs(session, "x") == (D(2026, 7, 10), "DAY", "IMPORTED")


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
    assert _fs(session, "a") == (D(2026, 7, 11), "DAY", "IMPORTED")


def test_first_seen_never_moves_later(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 11))
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 15))
    assert _fs(session, "a")[0] == D(2026, 7, 11)


def test_same_date_day_beats_week_in_either_order(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    # 周报先、日报后
    _imp(session, tmp_path, "w1", ["p"], D(2026, 7, 12), period="WEEK")
    _imp(session, tmp_path, "d1", ["p"], D(2026, 7, 12))
    # 日报先、周报后
    _imp(session, tmp_path, "d2", ["q"], D(2026, 7, 13))
    _imp(session, tmp_path, "w2", ["q"], D(2026, 7, 13), period="WEEK")
    assert _fs(session, "p") == (D(2026, 7, 12), "DAY", "IMPORTED")
    assert _fs(session, "q") == (D(2026, 7, 13), "DAY", "IMPORTED")


def test_late_daily_report_refines_earlier_weekly_first_seen(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 1))
    _imp(session, tmp_path, "w", ["c"], D(2026, 7, 18), period="WEEK")
    assert _fs(session, "c") == (D(2026, 7, 18), "WEEK", "IMPORTED")
    _imp(session, tmp_path, "d", ["c"], D(2026, 7, 15))
    assert _fs(session, "c") == (D(2026, 7, 15), "DAY", "IMPORTED")


def test_sites_are_independent(session, tmp_path):
    _imp(session, tmp_path, "us1", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "gb1", ["x"], D(2026, 7, 10), site="GB")
    _imp(session, tmp_path, "us2", ["x", "z"], D(2026, 7, 11))
    _imp(session, tmp_path, "gb2", ["x", "z"], D(2026, 7, 12), site="GB")
    assert _fs(session, "x", "GB") == (D(2026, 7, 10), "DAY", "IMPORTED")
    assert _fs(session, "z", "US") == (D(2026, 7, 11), "DAY", "IMPORTED")
    assert _fs(session, "z", "GB") == (D(2026, 7, 12), "DAY", "IMPORTED")


def test_legacy_unknown_word_becomes_earliest_observed_never_imported(session, tmp_path):
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'old')"))
    assert _fs(session, "old") == (None, None, "UNKNOWN")
    _imp(session, tmp_path, "a", ["old", "fresh"], D(2026, 7, 10))
    assert _fs(session, "old") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")  # 存量词
    assert _fs(session, "fresh") == (D(2026, 7, 10), "DAY", "IMPORTED")
    _imp(session, tmp_path, "b", ["old", "newer"], D(2026, 7, 11))
    assert _fs(session, "newer")[2] == "IMPORTED"
    assert _fs(session, "old")[2] == "EARLIEST_OBSERVED"


def test_imported_is_never_downgraded_by_earlier_report(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 12))
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 5))
    assert _fs(session, "a") == (D(2026, 7, 5), "DAY", "IMPORTED")


def test_first_seen_survives_fact_cleanup(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["a"], D(2026, 7, 11))
    session.execute(text("DELETE FROM analysis.fact_daily"))  # 模拟保留清理移出分区
    _imp(session, tmp_path, "c", ["a"], D(2026, 7, 20))
    assert _fs(session, "a") == (D(2026, 7, 11), "DAY", "IMPORTED")


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
    # 监测起点当天的词永远不算
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

    total = session.execute(crud.count_filtered_stmt("US", "DAY", D(2026, 7, 11), req)).scalar_one()
    assert total == _count(session, "DAY", D(2026, 7, 11)) == 2

    page = crud.search_stmt("US", "DAY", D(2026, 7, 11), req)
    rows = session.execute(page).all()
    assert {r.keyword for r in rows} == {"n1", "n2"}
    assert all(r.first_listed and r.first_seen_basis == "IMPORTED" for r in rows)

    # 不带筛选时全部行都在，只有首次入榜的两行带标记
    all_rows = session.execute(
        crud.search_stmt("US", "DAY", D(2026, 7, 11),
                         AnalysisSearchRequest(site="US", view="DAY", date=D(2026, 7, 11)))
    ).all()
    flags = {r.keyword: (r.first_listed, r.first_seen_basis) for r in all_rows}
    assert flags["base"] == (False, "IMPORTED")  # 监测起点当天的词，没有对照期
    assert flags["n1"] == (True, "IMPORTED")


def test_monitoring_start_only_counts_imported_rows(session, tmp_path):
    """监测起点 B = 导入创建的词的最早首次日期；回填写的存量词（更早日期）不得移动它，
    否则历史每天都「可判断」却一个导入创建的词都没有，会显示成 0。"""
    from app.table.analysis import analysis_crud as crud

    def start():
        return session.execute(crud.imported_since_stmt("US")).scalar()

    assert start() is None
    session.execute(text(
        "INSERT INTO analysis.dim_keyword (site, keyword, first_seen_date, first_seen_period_type,"
        " first_seen_basis) VALUES ('US', 'legacy', '2026-04-01', 'DAY', 'EARLIEST_OBSERVED')"
    ))
    assert start() is None
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    assert start() == D(2026, 7, 10)
    _imp(session, tmp_path, "b", ["y"], D(2026, 7, 5))  # 补更早报告：B 前移
    assert start() == D(2026, 7, 5)


def test_first_import_of_a_site_has_no_first_listed(session, tmp_path):
    """新站点首导：整期都是监测起点，没有对照期，不能涌进首次入榜。"""
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 7, 10))
    assert _count(session, "DAY", D(2026, 7, 10)) == 0
    _imp(session, tmp_path, "b", ["x", "z"], D(2026, 7, 11))
    assert _count(session, "DAY", D(2026, 7, 11)) == 1


def test_week_window_must_start_after_monitoring_start(session, tmp_path):
    """周视图 w 的窗口 [w-6, w] 含监测起点时只有 B 之后几天可判，计数偏少 → 整周不可判断。"""
    _imp(session, tmp_path, "d10", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "d12", ["x", "y"], D(2026, 7, 12))
    _imp(session, tmp_path, "w13", ["x", "y"], D(2026, 7, 13), period="WEEK")  # 窗口 7/7–7/13 含 B
    _imp(session, tmp_path, "w18", ["x", "y", "z"], D(2026, 7, 18), period="WEEK")  # 窗口 7/12–7/18
    assert _count(session, "WEEK", D(2026, 7, 13)) == 0
    assert _count(session, "WEEK", D(2026, 7, 18)) == 2  # y(7/12 日报) + z(7/18 周报)


# ---------------------------------------------------------------------------
# 顺序无关性（验收核心）：同一组报告以任何顺序导入，库内最终状态与每期数量逐位相同。
# ---------------------------------------------------------------------------

_REPORTS = [
    ("d10", ["a"], D(2026, 7, 10), "DAY"),
    ("d12", ["a", "b", "e"], D(2026, 7, 12), "DAY"),
    ("d14", ["a", "b", "c"], D(2026, 7, 14), "DAY"),
    ("w18", ["a", "b", "c", "d"], D(2026, 7, 18), "WEEK"),
]
_VIEWS = [("DAY", D(2026, 7, 10)), ("DAY", D(2026, 7, 12)), ("DAY", D(2026, 7, 14)),
          ("WEEK", D(2026, 7, 18))]


def _snapshot(session):
    rows = session.execute(text(
        "SELECT keyword, first_seen_date, first_seen_period_type, first_seen_basis "
        "FROM analysis.dim_keyword WHERE site='US' ORDER BY keyword"
    )).all()
    counts = [_count(session, view, on) for view, on in _VIEWS]
    return [tuple(r) for r in rows], counts


def _run(session, tmp_path, order, *, repeat_at=None):
    """按 order 依次导入；repeat_at 为下标时，在那一步之后把刚导入的报告再导一遍。"""
    for i, idx in enumerate(order):
        tag, names, d, period = _REPORTS[idx]
        _imp(session, tmp_path, f"{tag}_{i}", names, d, period=period)
        if repeat_at == i:
            _imp(session, tmp_path, f"{tag}_rep{i}", names, d, period=period)
    return _snapshot(session)


def test_import_order_does_not_change_result(session, tmp_path):
    import itertools

    expected = _run_in_savepoint(session, tmp_path, (0, 1, 2, 3))
    keywords = {r[0]: r for r in expected[0]}
    # 按时间顺序的预期值本身要对：b 与 e 首次出现在 7/12 日报，c 在 7/14，d 只在周报 7/18
    assert keywords["b"][1:] == (D(2026, 7, 12), "DAY", "IMPORTED")
    assert keywords["e"][1:] == (D(2026, 7, 12), "DAY", "IMPORTED")
    assert keywords["c"][1:] == (D(2026, 7, 14), "DAY", "IMPORTED")
    assert keywords["d"][1:] == (D(2026, 7, 18), "WEEK", "IMPORTED")
    # 7/10 是监测起点（无法判断，计 0）；日 7/12:b,e；日 7/14:c；周 7/18（窗口 7/12–7/18）:b,c,d
    # （e 首次在窗口内，但周报里没有它，周视图只数当期在榜的词）
    assert expected[1] == [0, 2, 1, 3]

    for perm in itertools.permutations(range(len(_REPORTS))):
        assert _run_in_savepoint(session, tmp_path, perm) == expected, f"导入顺序 {perm}"
        for repeat_at in (0, 2):
            got = _run_in_savepoint(session, tmp_path, perm, repeat_at=repeat_at)
            assert got == expected, f"导入顺序 {perm}，第 {repeat_at} 步后重复导入"


def _run_in_savepoint(session, tmp_path, order, *, repeat_at=None):
    nested = session.begin_nested()
    try:
        return _run(session, tmp_path, order, repeat_at=repeat_at)
    finally:
        nested.rollback()


def test_backfilled_earlier_report_lets_baseline_words_show_up(session, tmp_path):
    """先导入 7/12 再补更早的 7/10：只出现在 7/12 的词 E 原本落在监测起点当天，补完后监测起点
    前移到 7/10，E 应出现在 7/12 的首次入榜里——与按时间顺序导入的结果相同。"""
    _imp(session, tmp_path, "late", ["a", "e"], D(2026, 7, 12))
    assert _count(session, "DAY", D(2026, 7, 12)) == 0  # 此刻 7/12 就是监测起点
    _imp(session, tmp_path, "early", ["a"], D(2026, 7, 10))
    assert _count(session, "DAY", D(2026, 7, 12)) == 1  # e
    assert _fs(session, "e") == (D(2026, 7, 12), "DAY", "IMPORTED")


def test_watch_row_without_current_fact_is_not_first_listed(session, tmp_path):
    """词库盯盘周视图：词的首次记录落在周窗口内（日报来源），但所选周报里没有它——事实行为空
    （该期无数据），首次入榜标记必须为假，否则会显示「— 首次入榜」紧挨着「该期无数据」。
    （PR #141 复审 P2）"""
    from app.table.keyword import library_crud

    _imp(session, tmp_path, "d10", ["base"], D(2026, 7, 10))
    _imp(session, tmp_path, "d12", ["base", "w"], D(2026, 7, 12))
    _imp(session, tmp_path, "w18", ["base"], D(2026, 7, 18), period="WEEK")  # 周报里没有 w
    uid = session.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES ('fs-watch','x',true,false) RETURNING id")).scalar()
    gid = session.execute(
        text("INSERT INTO analysis.keyword_group(user_id,site,name) VALUES (:u,'US','g') "
             "RETURNING group_id"), {"u": uid}).scalar()
    session.execute(text(
        "INSERT INTO analysis.keyword_group_item(group_id,keyword_id) "
        "SELECT :g, keyword_id FROM analysis.dim_keyword WHERE keyword = 'w'"), {"g": gid})

    stmt = library_crud.watch_stmt(gid, "US", "WEEK", D(2026, 7, 18), order_by="current_ranking",
                                   order_dir="asc", offset=0, limit=10)
    rows = library_crud.shape_watch_rows(session.execute(stmt).all())
    assert [r["status"] for r in rows] == ["MISSING"]
    assert not rows[0].get("first_listed")
