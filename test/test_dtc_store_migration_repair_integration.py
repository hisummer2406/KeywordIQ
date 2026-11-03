"""迁移 c4e8f1a2d6b9 的数据修复语句（change fix-dtc-catalog-collection D6）。

在回滚事务里直接执行迁移模块的 ``REPAIR_SQL``，不在共享测试库上来回 downgrade/upgrade
——那会打断并行会话正在跑的集成测试。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住用户决定的修复口径：现有商品全部算基线（完整站点基线 = 迁移时刻，其余等首次完整
拉取）；被 429 误判为「未开放商品接口」的站点恢复可拉取，真正的 404 不动；已下架商品
补下架事件且可重复执行。
"""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _repair_sql() -> tuple[str, ...]:
    path = next(VERSIONS.glob("c4e8f1a2d6b9_*.py"))
    spec = importlib.util.spec_from_file_location("migration_c4e8f1a2d6b9", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.REPAIR_SQL


@pytest.fixture()
def conn():
    engine = create_engine(TEST_URL, poolclass=NullPool)
    connection = engine.connect()
    trans = connection.begin()
    try:
        if connection.execute(
            text("SELECT to_regclass('analysis.competitor_store_product_event')")
        ).scalar() is None:
            pytest.skip("测试库未迁移到 head，请先运行 alembic upgrade head")
        yield connection
    finally:
        trans.rollback()
        connection.close()
        engine.dispose()


WAS = datetime(2026, 10, 1, tzinfo=UTC)


def _store(conn, domain, *, complete, json_ok=True, error=None, status="OK"):
    conn.execute(
        text(
            "INSERT INTO analysis.competitor_store(domain, platform, catalog_complete, "
            "products_json_ok, last_error, last_fetch_status, last_fetch_at, baseline_at) "
            "VALUES (:d, 'SHOPIFY', :c, :ok, :e, :s, :was, :was)"
        ),
        {"d": domain, "c": complete, "ok": json_ok, "e": error, "s": status, "was": WAS},
    )


def _row(conn, domain):
    return conn.execute(
        text(
            "SELECT baseline_at, products_json_ok, last_fetch_status, last_fetch_at "
            "FROM analysis.competitor_store WHERE domain=:d"
        ),
        {"d": domain},
    ).one()


def test_修复按用户决定的口径改写站点并补下架事件(conn):
    _store(conn, "m-complete.example", complete=True)
    _store(conn, "m-partial.example", complete=False, status="FAILED",
           error="目录第 3 页拉取失败：Client error '429 Too Many Requests'")
    _store(conn, "m-throttled.example", complete=False, json_ok=False, status="NO_CATALOG_API",
           error="目录端点不可用：Client error '429 Too Many Requests' for url ...")
    _store(conn, "m-closed.example", complete=False, json_ok=False, status="NO_CATALOG_API",
           error="目录端点不可用：Client error '404 Not Found' for url ...")
    delisted_at = datetime(2026, 10, 20, tzinfo=UTC)
    for pid, gone in ((1, delisted_at), (2, None)):
        conn.execute(
            text(
                "INSERT INTO analysis.competitor_store_product(domain, product_id, handle, title, "
                "first_seen_at, delisted_at) VALUES ('m-complete.example', :p, :h, 't', :fs, :g)"
            ),
            {"p": pid, "h": f"h{pid}", "fs": WAS, "g": gone},
        )

    before = conn.execute(text("SELECT now()")).scalar()
    for statement in _repair_sql():
        conn.execute(text(statement))

    complete = _row(conn, "m-complete.example")
    assert complete.baseline_at >= before  # 迁移时刻：现有商品全部算基线
    assert _row(conn, "m-partial.example").baseline_at is None  # 等首次完整拉取

    throttled = _row(conn, "m-throttled.example")
    assert throttled.products_json_ok is True
    assert throttled.last_fetch_status == "FAILED"
    assert throttled.last_fetch_at is None  # 下一轮优先重拉

    closed = _row(conn, "m-closed.example")
    assert closed.products_json_ok is False  # 真正关了接口的不动
    assert closed.last_fetch_status == "NO_CATALOG_API"

    events = conn.execute(
        text(
            "SELECT product_id, kind, occurred_at FROM analysis.competitor_store_product_event "
            "WHERE domain='m-complete.example'"
        )
    ).all()
    assert [tuple(e) for e in events] == [(1, "DELISTED", delisted_at)]

    # 再执行一遍不产生重复事件
    for statement in _repair_sql():
        conn.execute(text(statement))
    assert conn.execute(
        text(
            "SELECT count(*) FROM analysis.competitor_store_product_event "
            "WHERE domain='m-complete.example'"
        )
    ).scalar() == 1
