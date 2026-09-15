"""database.py 的慢查询事件监听：阈值判定与日志内容的纯逻辑单元测试。

用一次性内存 SQLite 引擎验证 ``_register_slow_query_listener`` 本身的行为（阈值比较、
只记语句文本不记参数），不依赖真实 PostgreSQL——真正验证"异步引擎也会触发"这件事
需要真实数据库连接，见 test_slow_query_integration.py。
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

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


def test_failed_query_exceeding_threshold_logs_warning(caplog, monkeypatch):
    """语句执行失败（这里引用不存在的表来触发）不会走到 ``after_cursor_execute``
    ——DBAPI 根本没有成功返回，此前对"执行失败的慢查询"完全没有记录，即便它在真正
    失败之前已经等待了很久（语句超时/锁等待超时/连接池 checkout 超时都是这个形状，
    Codex 复审 PR #98 P2）。原始异常必须原样传播，不能被这个监听器吞掉或改写。"""
    monkeypatch.setattr(database.settings, "SLOW_QUERY_THRESHOLD_SECONDS", 0.0)
    engine = create_engine("sqlite:///:memory:")
    database._register_slow_query_listener(engine)

    with caplog.at_level(logging.WARNING, logger="database"):
        with engine.connect() as conn:
            with pytest.raises(OperationalError, match="nonexistent_table_xyz"):
                conn.execute(text("SELECT * FROM nonexistent_table_xyz"))

    warnings = [r for r in caplog.records if "慢查询检测（执行失败）" in r.message]
    assert len(warnings) == 1
    assert "nonexistent_table_xyz" in warnings[0].message


def test_failed_query_under_threshold_does_not_log(caplog, monkeypatch):
    monkeypatch.setattr(database.settings, "SLOW_QUERY_THRESHOLD_SECONDS", 60.0)
    engine = create_engine("sqlite:///:memory:")
    database._register_slow_query_listener(engine)

    with caplog.at_level(logging.WARNING, logger="database"):
        with engine.connect() as conn:
            with pytest.raises(OperationalError):
                conn.execute(text("SELECT * FROM nonexistent_table_xyz"))

    warnings = [r for r in caplog.records if "慢查询检测" in r.message]
    assert not warnings
