"""database.py 的慢查询事件监听：阈值判定与日志内容的纯逻辑单元测试。

用一次性内存 SQLite 引擎验证 ``_register_slow_query_listener`` 本身的行为（阈值比较、
只记语句文本不记参数），不依赖真实 PostgreSQL——真正验证"异步引擎也会触发"这件事
需要真实数据库连接，见 test_slow_query_integration.py。
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, text

import database


def test_query_exceeding_threshold_logs_warning(caplog, monkeypatch):
    monkeypatch.setattr(database.settings, "SLOW_QUERY_THRESHOLD_SECONDS", 0.0)
    engine = create_engine("sqlite:///:memory:")
    database._register_slow_query_listener(engine)

    with caplog.at_level(logging.WARNING, logger="database"):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert len(warnings) == 1
    assert "SELECT 1" in warnings[0].message


def test_query_under_threshold_does_not_log(caplog, monkeypatch):
    monkeypatch.setattr(database.settings, "SLOW_QUERY_THRESHOLD_SECONDS", 60.0)
    engine = create_engine("sqlite:///:memory:")
    database._register_slow_query_listener(engine)

    with caplog.at_level(logging.WARNING, logger="database"):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert not warnings


def test_long_statement_is_truncated_in_log(caplog, monkeypatch):
    monkeypatch.setattr(database.settings, "SLOW_QUERY_THRESHOLD_SECONDS", 0.0)
    engine = create_engine("sqlite:///:memory:")
    database._register_slow_query_listener(engine)

    long_literal = "1" + "1" * 300
    with caplog.at_level(logging.WARNING, logger="database"):
        with engine.connect() as conn:
            conn.execute(text(f"SELECT {long_literal}"))

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert len(warnings) == 1
    assert "..." in warnings[0].message
