"""``scripts/backfill_first_seen.py`` 的集成测试（需 TEST_DATABASE_URL）。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from scripts import backfill_first_seen as bf
from test import test_first_seen_integration as _fsi
from test.test_first_seen_integration import _fs
from test.test_importer_integration import pytestmark  # noqa: F401

session = _fsi.session

D = date


def _imp(session, tmp_path, tag, names, d, *, period="DAY"):
    """导入并登记 site_data_stats 与已完成批次——回填靠前者列出库内仍有数据的期次，证据
    重放靠后者做完整性校验（生产里由 import_service 写，这里直接调 import_report，要自己补）。"""
    from app.table.upload.import_batch_model import STATUS_COMPLETED, ImportBatch

    _fsi._imp(session, tmp_path, tag, names, d, period=period)
    session.execute(
        text("INSERT INTO analysis.site_data_stats (site, period_type, report_date, row_count, "
             "dropped_count) VALUES ('US', :p, :d, 0, 0) ON CONFLICT DO NOTHING"),
        {"p": period, "d": d},
    )
    session.add(ImportBatch(site="US", period_type=period, report_date=d, status=STATUS_COMPLETED,
                            file_name=f"{tag}.csv", written_rows=len(names)))
    session.flush()


def _reset_to_legacy(session):
    """把 dim_keyword 还原成迁移后的存量形态：全部 UNKNOWN、日期为空。"""
    session.execute(text(
        "UPDATE analysis.dim_keyword SET first_seen_date=NULL, first_seen_period_type=NULL, "
        "first_seen_basis='UNKNOWN'"
    ))


def test_backfill_gives_earliest_observed_never_confirmed(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "y"], D(2026, 7, 11))
    _imp(session, tmp_path, "w", ["z"], D(2026, 7, 18), period="WEEK")
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'ghost')"))
    _reset_to_legacy(session)

    stats = bf.backfill_site(session, "US", dry_run=False)

    assert stats["periods"] == 3
    assert _fs(session, "x") == (D(2026, 7, 10), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "y") == (D(2026, 7, 11), "DAY", "EARLIEST_OBSERVED")
    assert _fs(session, "z") == (D(2026, 7, 18), "WEEK", "EARLIEST_OBSERVED")
    # 库内没有任何事实证据的词保持未知，不被推断
    assert _fs(session, "ghost") == (None, None, "UNKNOWN")
    assert "IMPORTED" not in bf.basis_counts(session, "US")


def test_backfill_is_idempotent(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "y"], D(2026, 7, 11))
    _reset_to_legacy(session)
    first = bf.backfill_site(session, "US", dry_run=False)
    second = bf.backfill_site(session, "US", dry_run=False)
    assert first["updated"] == 2
    assert second["updated"] == 0


def test_backfill_does_not_touch_confirmed_basis(session, tmp_path):
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["c"], D(2026, 7, 12))  # c 是 IMPORTED
    bf.backfill_site(session, "US", dry_run=False)
    assert _fs(session, "c") == (D(2026, 7, 12), "DAY", "IMPORTED")


def test_sample_check_reports_no_mismatch(session, tmp_path):
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 7, 10))
    _reset_to_legacy(session)
    bf.backfill_site(session, "US", dry_run=False)
    total, bad = bf.sample_check(session, "US", 10)
    assert (total, bad) == (2, 0)


def test_dry_run_reports_real_counts(session, tmp_path):
    """dry-run 每期真跑再回滚，所以报出的数字是真的。（夹具把会话绑在外层事务上，回滚会
    连同夹具造的数据一起撤销，因此这里只验证数字，不再读库——「库不变」由回滚本身保证。）"""
    _imp(session, tmp_path, "a", ["x", "y"], D(2026, 7, 10))
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=True)
    assert (stats["periods"], stats["updated"]) == (1, 2)


def test_dry_run_does_not_recount_a_keyword_seen_in_several_periods(session, tmp_path):
    """x 出现在两期：正式跑只会在较早那期改它一次，dry-run 的数字必须与之相等。

    逐期回滚时第二期从原状态重来，x 被算两次（updated=3）；统一回滚才是 2
    （x 一次 + 只在第二期出现的 y 一次）。"""
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "y"], D(2026, 7, 11))
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=True)
    assert (stats["periods"], stats["updated"]) == (2, 2)


# ---- 证据重放（--replay）----

# 周报日期是周起始日；日报先于周报、乱序导入，结果都应与按时间顺序逐期导入一致。
_HISTORY = [
    ("w1", ["a", "b"], D(2026, 7, 4), "WEEK"),
    ("d6", ["a", "c"], D(2026, 7, 6), "DAY"),
    ("d8", ["b", "f"], D(2026, 7, 8), "DAY"),   # b 掉榜后回归，不重置
    ("d7", ["c", "d"], D(2026, 7, 7), "DAY"),   # 晚到的更早一期
    ("w2", ["d", "e", "a"], D(2026, 7, 11), "WEEK"),
    ("d11", ["e"], D(2026, 7, 11), "DAY"),       # 同一日期日报优先于周报
]


def _import_history(session, tmp_path):
    for tag, names, d, period in _HISTORY:
        _imp(session, tmp_path, tag, names, d, period=period)


def _snapshot(session):
    return {r[0]: tuple(r[1:]) for r in session.execute(text(
        "SELECT keyword, first_seen_date, first_seen_period_type, first_seen_basis "
        "FROM analysis.dim_keyword WHERE site = 'US'"
    )).all()}


def test_replay_matches_sequential_import(session, tmp_path):
    """逐期导入的结果 vs 清空成存量形态后重放：日期与周期逐词相同；来源除 B0 当期记最早
    可观测外也相同。"""
    _import_history(session, tmp_path)
    session.execute(text("INSERT INTO analysis.dim_keyword (site, keyword) VALUES ('US', 'ghost')"))
    imported = _snapshot(session)
    _reset_to_legacy(session)

    stats = bf.backfill_site(session, "US", dry_run=False, replay=True)

    b0 = D(2026, 7, 4)
    assert stats["b0"] == b0
    replayed = _snapshot(session)
    assert replayed.pop("ghost") == (None, None, "UNKNOWN")
    imported.pop("ghost")
    expected = {
        k: (d, p, "EARLIEST_OBSERVED" if d == b0 else basis)
        for k, (d, p, basis) in imported.items()
    }
    assert replayed == expected
    assert replayed["e"] == (D(2026, 7, 11), "DAY", "IMPORTED")
    assert replayed["d"] == (D(2026, 7, 7), "DAY", "IMPORTED")
    assert bf.monitoring_start(session, "US") == D(2026, 7, 6)


def test_replay_rerun_updates_nothing(session, tmp_path):
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    first = bf.backfill_site(session, "US", dry_run=False, replay=True)
    after_first = _snapshot(session)
    second = bf.backfill_site(session, "US", dry_run=False, replay=True)
    assert first["updated"] == len(after_first)
    assert second["updated"] == 0
    assert _snapshot(session) == after_first


def test_replay_dry_run_reports_same_count_as_real_run(session, tmp_path):
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=True, replay=True)
    assert stats["updated"] == 6  # a b c d e f，各在第一次出现那期改一次


def test_replay_refuses_when_earliest_period_was_pruned(session, tmp_path):
    """最早已完成批次的事实不在了（保留清理 DROP 了分区）：B0 会被误认成更晚的一期，
    那期的存量词全被标成导入创建——必须拒绝，且一行不改。"""
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    session.execute(text("DELETE FROM analysis.fact_weekly WHERE report_date_week = '2026-07-04'"))
    session.execute(text("DELETE FROM analysis.site_data_stats "
                         "WHERE period_type = 'WEEK' AND report_date = '2026-07-04'"))
    before = _snapshot(session)

    try:
        bf.backfill_site(session, "US", dry_run=False, replay=True)
    except bf.EvidenceIncomplete as exc:
        gaps = exc.gaps
    else:
        raise AssertionError("证据不完整时应拒绝重放")

    assert any("最早期次 2026-07-11" in g for g in gaps)
    assert any("事实已不在库内" in g and "2026-07-04" in g for g in gaps)
    assert _snapshot(session) == before


def test_replay_refuses_when_completed_batch_is_not_listed(session, tmp_path):
    """site_data_stats 漏登记的期次重放时会被跳过，同样拒绝。"""
    _import_history(session, tmp_path)
    session.execute(text("DELETE FROM analysis.site_data_stats "
                         "WHERE period_type = 'DAY' AND report_date = '2026-07-07'"))
    gaps = bf.evidence_gaps(session, "US")
    assert len(gaps) == 1 and "site_data_stats" in gaps[0] and "2026-07-07" in gaps[0]


def test_evidence_gaps_pass_on_complete_history(session, tmp_path):
    _import_history(session, tmp_path)
    assert bf.evidence_gaps(session, "US") == []


def test_undo_restores_only_words_still_at_backfilled_value(session, tmp_path):
    """回填后 c 被新导入改过（这里直接改值模拟），撤销保持 c 的新值，其余恢复原状态。"""
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=False, replay=True)
    session.execute(text(
        "UPDATE analysis.dim_keyword SET first_seen_date = '2026-07-05' WHERE keyword = 'c'"
    ))

    restored = bf.undo(session, stats["backup"])

    snap = _snapshot(session)
    assert restored == 5
    assert snap.pop("c") == (D(2026, 7, 5), "DAY", "IMPORTED")
    assert set(snap.values()) == {(None, None, "UNKNOWN")}


def test_backup_keeps_original_state_across_periods(session, tmp_path):
    """a 在多期出现但只在第一次出现那期被改；回滚表里的原状态是回填前的 UNKNOWN。"""
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=False, replay=True)
    row = session.execute(text(
        f"SELECT b.old_date, b.old_basis, b.new_date, b.new_basis FROM analysis.{stats['backup']} b "
        "JOIN analysis.dim_keyword k USING (keyword_id) WHERE k.keyword = 'a'"
    )).one()
    assert tuple(row) == (None, "UNKNOWN", D(2026, 7, 4), "EARLIEST_OBSERVED")


def test_replay_sample_check_covers_both_bases(session, tmp_path):
    _import_history(session, tmp_path)
    _reset_to_legacy(session)
    bf.backfill_site(session, "US", dry_run=False, replay=True)
    total, bad = bf.sample_check(session, "US", 10, bases=("EARLIEST_OBSERVED", "IMPORTED"))
    assert (total, bad) == (6, 0)


def test_backup_name_rejects_injection():
    import pytest

    with pytest.raises(ValueError):
        bf.undo(None, "bak_first_seen_x; DROP TABLE analysis.dim_keyword")
