"""迁移脚本（`migrate_to_selfhosted_pg.py`）前置检查的数据库集成测试。

回归目标：`_check_no_active_import` 被调用两次，语义不同——首次（`_quiesce_source`
之前）拦的是"迁移前源库本就有导入在跑"；`after_quiesce=True`（`_quiesce_source`
之后）拦的是本脚本自己制造的竞态：一个导入恰好在"检查通过"到"断开连接"这段窗口内
开始，被切断连接后永久卡在 `PROCESSING`（源库此刻已只读，它的失败处理写不进新状态），
且会连同其余数据被 `pg_dump --data-only` 原样搬到目标库，没有任何环节会报错
（Codex 审核 PR #84 第九轮）。两种情况下"等它跑完"这句话只对首次成立，第二次必须给出
不同的、指向人工核实的提示，不能复用同一句话误导操作者。

**默认跳过**，仅在设置 `TEST_DATABASE_URL` 时运行。见 test/test_v2_integration.py
同款说明与 .claude/skills/local-db/SKILL.md。
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from scripts.migrate_to_selfhosted_pg import _check_no_active_import

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

_INSERT_BATCH = text(
    """
    INSERT INTO analysis.import_batch (site, period_type, report_date, file_name, status)
    VALUES (:site, 'DAY', :report_date, 'probe.csv', :status)
    RETURNING id
    """
)


def test_没有活跃批次时两种调用都放行():
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM analysis.import_batch WHERE file_name = 'probe.csv'"))

    _check_no_active_import(TEST_URL)
    _check_no_active_import(TEST_URL, after_quiesce=True)


def test_首次检查提示等待跑完():
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM analysis.import_batch WHERE file_name = 'probe.csv'"))
        batch_id = conn.execute(
            _INSERT_BATCH, {"site": "US", "report_date": date(2031, 6, 1), "status": "PROCESSING"}
        ).scalar()

    try:
        with pytest.raises(SystemExit, match="先等它们跑完"):
            _check_no_active_import(TEST_URL)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis.import_batch WHERE id = :id"), {"id": batch_id})


def test_断开连接后复查给出不同的人工核实提示():
    """措辞必须与首次检查不同：连接已断、源库已只读，这条批次不会自己跑完。"""
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM analysis.import_batch WHERE file_name = 'probe.csv'"))
        batch_id = conn.execute(
            _INSERT_BATCH, {"site": "US", "report_date": date(2031, 6, 1), "status": "PROCESSING"}
        ).scalar()

    try:
        with pytest.raises(SystemExit, match="不会自己恢复"):
            _check_no_active_import(TEST_URL, after_quiesce=True)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis.import_batch WHERE id = :id"), {"id": batch_id})
