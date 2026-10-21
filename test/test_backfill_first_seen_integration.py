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
    """导入并登记 site_data_stats——回填靠它列出库内仍有数据的期次（生产里由
    import_service 在导入收尾时写，这里的测试直接调 import_report，要自己补）。"""
    _fsi._imp(session, tmp_path, tag, names, d, period=period)
    session.execute(
        text("INSERT INTO analysis.site_data_stats (site, period_type, report_date, row_count, "
             "dropped_count) VALUES ('US', :p, :d, 0, 0) ON CONFLICT DO NOTHING"),
        {"p": period, "d": d},
    )


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
    assert "CONFIRMED" not in bf.basis_counts(session, "US")


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
    _imp(session, tmp_path, "b", ["c"], D(2026, 7, 12))  # c 是 CONFIRMED
    bf.backfill_site(session, "US", dry_run=False)
    assert _fs(session, "c") == (D(2026, 7, 12), "DAY", "CONFIRMED")


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
    assert stats == {"periods": 1, "updated": 2}


def test_dry_run_does_not_recount_a_keyword_seen_in_several_periods(session, tmp_path):
    """x 出现在两期：正式跑只会在较早那期改它一次，dry-run 的数字必须与之相等。

    逐期回滚时第二期从原状态重来，x 被算两次（updated=3）；统一回滚才是 2
    （x 一次 + 只在第二期出现的 y 一次）。"""
    _imp(session, tmp_path, "a", ["x"], D(2026, 7, 10))
    _imp(session, tmp_path, "b", ["x", "y"], D(2026, 7, 11))
    _reset_to_legacy(session)
    stats = bf.backfill_site(session, "US", dry_run=True)
    assert stats == {"periods": 2, "updated": 2}
