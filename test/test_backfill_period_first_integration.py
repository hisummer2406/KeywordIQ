"""周期首次证据回填（#178，keyword-first-seen「周期首次证据可按证据重放回填」）的真库测试。

核心断言：逐期乱序导入得到的周期证据 = 清空成存量形态（全 UNKNOWN）后集合式回填的结果（``B0_P``
当期的词除外：回填记最早可观测）。另测证据不完整拒绝且零修改、重复零更新、撤销不误伤、共享记录
与事实不变。跑法同 ``test_importer_integration.py``。
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from sqlalchemy import text

from scripts import backfill_period_first as bp
from test import test_backfill_first_seen_integration as _bfs
from test.test_importer_integration import pytestmark  # noqa: F401

session = _bfs.session
_imp = _bfs._imp

D = date

# 周报 7/4、7/11、7/18；日报 7/6、7/8、7/12、7/19（日榜起点 7/6）。
_HISTORY = [
    ("w04", ["a", "b"], D(2026, 7, 4), "WEEK"),
    ("d06", ["a"], D(2026, 7, 6), "DAY"),
    ("d08", ["a", "c"], D(2026, 7, 8), "DAY"),
    ("w11", ["a", "c", "d"], D(2026, 7, 11), "WEEK"),
    ("d12", ["b", "d"], D(2026, 7, 12), "DAY"),       # b 先上周榜（起点当期）后上日榜
    ("w18", ["e"], D(2026, 7, 18), "WEEK"),           # e 只上周榜
    ("d19", ["f", "e"], D(2026, 7, 19), "DAY"),
]


def _snapshot(session):
    return {r[0]: tuple(r[1:]) for r in session.execute(text(
        "SELECT keyword, day_first_date, day_first_basis, week_first_date, week_first_basis,"
        " first_seen_date, first_seen_period_type, first_seen_basis"
        " FROM analysis.dim_keyword WHERE site = 'US'"
    )).all()}


def _reset_period_to_legacy(session):
    session.execute(text(
        "UPDATE analysis.dim_keyword SET day_first_date=NULL, day_first_basis='UNKNOWN',"
        " week_first_date=NULL, week_first_basis='UNKNOWN'"
    ))


def _import(session, tmp_path, order):
    for i in order:
        tag, names, d, period = _HISTORY[i]
        _imp(session, tmp_path, f"{tag}_{i}", names, d, period=period)


def _expected_from_sequential(snapshot):
    """逐期导入的结果，B0_P 当期（日 7/6、周 7/4）的导入创建改为最早可观测。"""
    b0 = {"day": D(2026, 7, 6), "week": D(2026, 7, 4)}
    out = {}
    for k, (dd, db, wd, wb, *shared) in snapshot.items():
        db = "EARLIEST_OBSERVED" if db == "IMPORTED" and dd == b0["day"] else db
        wb = "EARLIEST_OBSERVED" if wb == "IMPORTED" and wd == b0["week"] else wb
        out[k] = (dd, db, wd, wb, *shared)
    return out


def test_backfill_matches_sequential_import(session, tmp_path):
    nested = session.begin_nested()
    _import(session, tmp_path, range(len(_HISTORY)))
    sequential = _snapshot(session)
    nested.rollback()
    expected = _expected_from_sequential(sequential)
    # 预期值本身对：b 日榜 7/12 导入创建，周榜 7/4 起点当期；e 日榜 7/19；f 从未上周榜
    assert expected["b"][:4] == (D(2026, 7, 12), "IMPORTED", D(2026, 7, 4), "EARLIEST_OBSERVED")
    assert expected["e"][:4] == (D(2026, 7, 19), "IMPORTED", D(2026, 7, 18), "IMPORTED")
    assert expected["f"][:4] == (D(2026, 7, 19), "IMPORTED", None, "ABSENT")

    # 乱序导入（任取几种）后清成存量形态再回填，结果都等于预期
    for order in list(itertools.permutations(range(len(_HISTORY))))[::997]:
        nested = session.begin_nested()
        try:
            _import(session, tmp_path, order)
            _reset_period_to_legacy(session)
            bp.backfill_site(session, "US", backup=bp.new_backup_name())
            assert _snapshot(session) == expected, f"导入顺序 {order}"
        finally:
            nested.rollback()


def test_rerun_updates_nothing(session, tmp_path):
    _import(session, tmp_path, range(len(_HISTORY)))
    _reset_period_to_legacy(session)
    assert bp.backfill_site(session, "US", backup=bp.new_backup_name())["updated"] == 6
    assert bp.backfill_site(session, "US", backup=bp.new_backup_name())["updated"] == 0


def test_refuses_when_evidence_incomplete_and_changes_nothing(session, tmp_path):
    _import(session, tmp_path, range(len(_HISTORY)))
    _reset_period_to_legacy(session)
    session.execute(text("DELETE FROM analysis.fact_daily WHERE report_date = '2026-07-06'"))
    before = _snapshot(session)
    with pytest.raises(bp.EvidenceIncomplete):
        bp.backfill_site(session, "US", backup=bp.new_backup_name())
    assert _snapshot(session) == before


def test_shared_record_and_facts_untouched(session, tmp_path):
    _import(session, tmp_path, range(len(_HISTORY)))
    shared = {k: v[4:] for k, v in _snapshot(session).items()}
    facts = session.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar()
    _reset_period_to_legacy(session)
    bp.backfill_site(session, "US", backup=bp.new_backup_name())
    assert {k: v[4:] for k, v in _snapshot(session).items()} == shared
    assert session.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == facts


def test_undo_restores_only_rows_still_at_backfilled_value(session, tmp_path):
    _import(session, tmp_path, range(len(_HISTORY)))
    _reset_period_to_legacy(session)
    legacy = _snapshot(session)
    backup = bp.new_backup_name()
    bp.backfill_site(session, "US", backup=backup)
    # 回填后新导入把 f 的日榜证据前移
    _imp(session, tmp_path, "d10", ["f"], D(2026, 7, 10))
    moved = _snapshot(session)["f"]
    assert moved[0] == D(2026, 7, 10)
    restored = bp.undo(session, backup)
    after = _snapshot(session)
    assert after["f"] == moved  # 不误伤
    assert all(after[k] == legacy[k] for k in legacy if k != "f")
    assert restored == 5


def test_sample_check_and_counts(session, tmp_path):
    _import(session, tmp_path, range(len(_HISTORY)))
    _reset_period_to_legacy(session)
    bp.backfill_site(session, "US", backup=bp.new_backup_name())
    total, bad = bp.sample_check(session, "US", 10)
    assert total > 0 and bad == 0
    counts = bp.basis_counts(session, "US")
    assert counts["day"].get("UNKNOWN", 0) == counts["week"].get("UNKNOWN", 0) == 0
    assert bp.period_starts(session, "US") == {"day": D(2026, 7, 8), "week": D(2026, 7, 11)}


def test_backup_name_rejects_injection():
    with pytest.raises(ValueError):
        bp.ensure_backup_table(None, "bak_period_first_x; DROP TABLE y")
