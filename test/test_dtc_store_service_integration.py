"""独立站监控写入层的集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。测试库必须已迁移到 head。每个测试独占
一个事务、结束即回滚（service 层内部不 commit，本文件直接调 ``apply_fetch_result``）。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_dtc_store_service_integration.py -v

这里钉的是六件**错了不会报错**的事：首刷不产生上新事件、``published_at`` 整体跳变不
产生假上新、价格未变不写、拉取不完整绝不判下架、同日名次重跑幂等、认领只取到期站点
并当场推进拉取时刻。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.table.competitor import store_crud, store_service
from app.table.competitor.store_fetcher import (
    FETCH_INCOMPLETE,
    FETCH_OK,
    ParsedProduct,
    ParsedStoreMeta,
    ParsedVariant,
    StoreFetchOutcome,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

DOMAIN = "dtc-test-store.example"
NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(TEST_URL, poolclass=NullPool)
    connection = engine.connect()
    # 先开事务再做任何查询——execute() 会自动开一个，之后再 begin() 就冲突了。
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        if session.execute(
            text("SELECT to_regclass('analysis.competitor_store')")
        ).scalar() is None:
            pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
        session.execute(
            text(
                "INSERT INTO analysis.competitor_store(domain, platform) VALUES (:d, 'SHOPIFY')"
            ),
            {"d": DOMAIN},
        )
        session.flush()
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
        engine.dispose()


def _variant(vid: int, price: str | None = "10.00", *, available: bool = True) -> ParsedVariant:
    return ParsedVariant(
        variant_id=vid,
        title="默认",
        sku=f"SKU{vid}",
        price=Decimal(price) if price is not None else None,
        compare_at_price=None,
        available=available,
    )


def _product(pid: int, *, published: datetime | None = None, variants=None) -> ParsedProduct:
    return ParsedProduct(
        product_id=pid,
        handle=f"p{pid}",
        title=f"商品 {pid}",
        vendor="v",
        product_type="t",
        tags=(),
        published_at=published or datetime(2026, 8, 20, tzinfo=UTC),
        created_at=published or datetime(2026, 8, 20, tzinfo=UTC),
        updated_at=NOW,
        image_url=None,
        variants=tuple(variants or [_variant(pid * 10)]),
    )


def _outcome(products, *, complete=True, ranks=(), rank_ok=None, status=None, meta=None):
    return StoreFetchOutcome(
        meta=meta,
        products=tuple(products),
        catalog_complete=complete,
        products_json_ok=True,
        rank_handles=tuple(ranks),
        rank_ok=bool(ranks) if rank_ok is None else rank_ok,
        status=status or (FETCH_OK if complete else FETCH_INCOMPLETE),
    )


def _apply(db, outcome, *, first=False, now=NOW):
    summary = store_service.apply_fetch_result(
        db, DOMAIN, outcome, now=now, is_first_fetch=first
    )
    db.flush()
    return summary


def _count(db, sql: str) -> int:
    return db.execute(text(sql), {"d": DOMAIN}).scalar()


# ---------- 新品判定 ----------


def test_首刷入库但不产生上新事件(db):
    """新纳入站点上个月发的商品不是「今天上新」，只是我们刚开始看。"""
    summary = _apply(db, _outcome([_product(1), _product(2)]), first=True)

    assert summary.new_products == 0
    assert _count(db, "SELECT count(*) FROM analysis.competitor_store_product WHERE domain=:d") == 2


def test_首刷之后历史立即可查(db):
    """published_at 原样入库，所以「近 7 天上了什么」当场就能查——不用等一个周期建基线。"""
    recent = datetime(2026, 8, 30, tzinfo=UTC)
    _apply(db, _outcome([_product(1, published=recent)]), first=True)

    stored = db.execute(
        text("SELECT published_at FROM analysis.competitor_store_product WHERE domain=:d"),
        {"d": DOMAIN},
    ).scalar()
    assert stored == recent


def test_第二次拉取里的新商品才算上新(db):
    _apply(db, _outcome([_product(1)]), first=True)
    summary = _apply(db, _outcome([_product(1), _product(2)]))

    assert summary.new_products == 1


def test_published_at_整体跳变不产生假上新(db):
    """商户批量重新发布会让 published_at 全部跳到当日。判据必须是「商品在不在库里」。"""
    _apply(db, _outcome([_product(1), _product(2)]), first=True)

    republished = [_product(1, published=NOW), _product(2, published=NOW)]
    summary = _apply(db, _outcome(republished))

    assert summary.new_products == 0


# ---------- 价格 ----------


def test_价格未变不写第二条(db):
    _apply(db, _outcome([_product(1)]), first=True)
    summary = _apply(db, _outcome([_product(1)]), now=NOW + timedelta(days=1))

    assert summary.price_changes == 0
    assert _count(db, "SELECT count(*) FROM analysis.competitor_store_price WHERE domain=:d") == 1


def test_价格变了才追加且保留历史(db):
    _apply(db, _outcome([_product(1, variants=[_variant(10, "10.00")])]), first=True)
    _apply(
        db,
        _outcome([_product(1, variants=[_variant(10, "8.50")])]),
        now=NOW + timedelta(days=1),
    )

    prices = db.execute(
        text(
            "SELECT price FROM analysis.competitor_store_price "
            "WHERE domain=:d ORDER BY observed_at"
        ),
        {"d": DOMAIN},
    ).scalars().all()
    assert prices == [Decimal("10.00"), Decimal("8.50")]


def test_是否有货变化也算变化(db):
    _apply(db, _outcome([_product(1, variants=[_variant(10, "10.00")])]), first=True)
    summary = _apply(
        db,
        _outcome([_product(1, variants=[_variant(10, "10.00", available=False)])]),
        now=NOW + timedelta(days=1),
    )

    assert summary.price_changes == 1


# ---------- 下架判定 ----------


def test_拉取不完整时绝不判下架(db):
    """一次网络抖动把整店判成清仓，是这条路唯一能造成大面积假数据的方式。"""
    _apply(db, _outcome([_product(1), _product(2)]), first=True)
    summary = _apply(db, _outcome([_product(1)], complete=False), now=NOW + timedelta(days=1))

    assert summary.delisted == 0
    assert _count(
        db,
        "SELECT count(*) FROM analysis.competitor_store_product "
        "WHERE domain=:d AND delisted_at IS NOT NULL",
    ) == 0


def test_完整拉取时缺席的商品判下架(db):
    _apply(db, _outcome([_product(1), _product(2)]), first=True)
    summary = _apply(db, _outcome([_product(1)]), now=NOW + timedelta(days=1))

    assert summary.delisted == 1


def test_重新上架清掉下架标记(db):
    _apply(db, _outcome([_product(1), _product(2)]), first=True)
    _apply(db, _outcome([_product(1)]), now=NOW + timedelta(days=1))
    summary = _apply(db, _outcome([_product(1), _product(2)]), now=NOW + timedelta(days=2))

    assert summary.relisted == 1
    # 重新上架不算新品——它本来就在库里
    assert summary.new_products == 0


# ---------- 名次 ----------


def test_名次按出现顺序落库(db):
    _apply(db, _outcome([_product(1), _product(2)], ranks=["p2", "p1"]), first=True)

    rows = db.execute(
        text(
            "SELECT rank, product_id FROM analysis.competitor_store_rank "
            "WHERE domain=:d ORDER BY rank"
        ),
        {"d": DOMAIN},
    ).all()
    assert rows == [(1, 2), (2, 1)]


def test_同日重跑名次幂等(db):
    _apply(db, _outcome([_product(1), _product(2)], ranks=["p1", "p2"]), first=True)
    _apply(db, _outcome([_product(1), _product(2)], ranks=["p2", "p1"]))

    rows = db.execute(
        text(
            "SELECT rank, product_id FROM analysis.competitor_store_rank "
            "WHERE domain=:d ORDER BY rank"
        ),
        {"d": DOMAIN},
    ).all()
    assert rows == [(1, 2), (2, 1)]  # 覆盖成第二次的顺序，不是叠加四行


def test_名次解析失败不影响商品与价格入库(db):
    summary = _apply(
        db, _outcome([_product(1)], ranks=[], rank_ok=False), first=True
    )

    assert summary.ranks_written == 0
    assert summary.price_changes == 1
    assert _count(db, "SELECT count(*) FROM analysis.competitor_store_product WHERE domain=:d") == 1


# ---------- 站点状态与认领 ----------


def test_meta_成功时回写店名与币种(db):
    """店名也要回写——商户改店名后界面得跟着变，且直接建行（不经 add_store）时它是空的。"""
    meta = ParsedStoreMeta(
        shop_id=1,
        name="X",
        currency="GBP",
        country="CN",
        myshopify_domain="x.myshopify.com",
        published_products_count=1,
        ships_to_countries=("GB",),
    )
    _apply(db, _outcome([_product(1)], meta=meta), first=True)

    row = db.execute(
        text("SELECT name, currency FROM analysis.competitor_store WHERE domain=:d"),
        {"d": DOMAIN},
    ).one()
    assert (row.name, row.currency) == ("X", "GBP")


def test_meta_失败时不清空库里既有币种(db):
    """meta 抖动不该把已知的币种抹掉——价格历史没有币种就再也解释不了。"""
    db.execute(
        text("UPDATE analysis.competitor_store SET currency='USD' WHERE domain=:d"),
        {"d": DOMAIN},
    )
    _apply(db, _outcome([_product(1)], meta=None), first=True)

    assert db.execute(
        text("SELECT currency FROM analysis.competitor_store WHERE domain=:d"), {"d": DOMAIN}
    ).scalar() == "USD"


def test_首刷落定_first_fetch_at_且此后不再改动(db):
    """上新流靠 first_fetch_at 把首刷那批排除在外，它一旦落定就不能被改。

    改了的表现是首刷那批商品突然涌进上新流——几百条「今天上新」，而没有任何地方会报错。
    """
    _apply(db, _outcome([_product(1)]), first=True)
    first = db.execute(
        text("SELECT first_fetch_at FROM analysis.competitor_store WHERE domain=:d"),
        {"d": DOMAIN},
    ).scalar()
    assert first == NOW

    # 后续拉取不传 first_fetch_at；即使误传，COALESCE 也保住既有值
    _apply(db, _outcome([_product(1)]), now=NOW + timedelta(days=1))
    _apply(db, _outcome([_product(1)]), first=True, now=NOW + timedelta(days=2))

    assert db.execute(
        text("SELECT first_fetch_at FROM analysis.competitor_store WHERE domain=:d"),
        {"d": DOMAIN},
    ).scalar() == NOW


def test_认领只取到期站点且当场推进拉取时刻(db):
    """last_fetch_at 记的是「上次尝试」——不推进的话，一个坏掉的站点会把轮询占满。"""
    assert store_crud.claim_due_store(db, interval_hours=24, now=NOW) == DOMAIN
    db.flush()

    # 刚认领过，24 小时内不该再被认领
    assert store_crud.claim_due_store(db, interval_hours=24, now=NOW) is None
