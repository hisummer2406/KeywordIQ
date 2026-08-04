"""三段式导入的数据库集成测试。

验证 COPY→维度→事实的完整流程在真实 PostgreSQL 上的行为：派生值计算、跨站点不覆盖、
跨日期不覆盖、覆盖语义、坏行跳过计数、is_new。单元测试无法验证 COPY 与集合式 SQL。

默认跳过，只在设置 ``TEST_DATABASE_URL`` 时运行（沿用数据模型变更的 gate 模式）。测试库
必须已迁移（alembic upgrade head）。每个测试独占事务、结束回滚，零残留。

运行::

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_importer_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行导入集成测试")

# 21 列的表头（内容不重要，导入按位置映射，但结构要真实：元数据行 + 表头行 + 数据）
_META = "报告范围=US,日期=2026-07-15"
_HEADER = ",".join(["搜索频率排名", "搜索词"] + [f"col{i}" for i in range(19)])


def _row(rank, keyword, *, a1="", t1="", b1="", cat1="", cs1="", a2="", t2="", b2="", cat2="",
         a3="", t3="", b3="", cat3="", date_str="2026-07-15") -> str:
    """按 21 列布局拼一行。留空的商品槽位模拟"无第 2/3 名商品"。"""
    cells = [str(rank), keyword, b1, b2, b3, cat1, cat2, cat3,
             a1, t1, cs1, "", a2, t2, "", "", a3, t3, "", "", date_str]
    # csv 转义：含逗号的标题加引号
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf).writerow(cells)
    return buf.getvalue().rstrip("\r\n")


def _write_csv(tmp_path: Path, name: str, rows: list[str]) -> str:
    p = tmp_path / name
    p.write_text("\n".join([_META, _HEADER, *rows]) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture()
def session():
    engine = create_engine(TEST_URL)
    conn = engine.connect()
    trans = conn.begin()
    if conn.execute(text("SELECT to_regclass('analysis.fact_daily')")).scalar() is None:
        trans.rollback(); conn.close(); engine.dispose()
        pytest.skip("测试库未迁移，请先 alembic upgrade head")
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()
        engine.dispose()


def _import(session, tmp_path, name, rows, *, site="US", period="DAY", d=date(2026, 7, 15)):
    from app.table.upload.import_source import LocalFileSource
    from app.table.upload.importer import import_report
    path = _write_csv(tmp_path, name, rows)
    return import_report(session, site=site, period=period, report_date=d, source=LocalFileSource(path))


def test_basic_import_writes_facts_and_dims(session, tmp_path):
    r = _import(session, tmp_path, "us_day.csv", [
        _row(1, "wireless mouse", a1="B001", t1="Logi Mouse", b1="Logitech", cat1="Electronics", cs1="12.5"),
        _row(2, "usb cable", a1="B002", t1="Anker Cable", b1="Anker", cat1="Electronics", cs1="8.0"),
    ])
    assert r.written_rows == 2
    assert session.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 2
    assert session.execute(text("SELECT count(*) FROM analysis.dim_keyword WHERE site='US'")).scalar() == 2
    assert session.execute(text("SELECT count(*) FROM analysis.dim_product")).scalar() == 2
    # 商品引用正确解析
    title = session.execute(text("""
        SELECT p.title FROM analysis.fact_daily f
        JOIN analysis.dim_product p ON p.product_id=f.top1_product_id
        JOIN analysis.dim_keyword k ON k.keyword_id=f.keyword_id
        WHERE k.keyword='wireless mouse'""")).scalar()
    assert title == "Logi Mouse"


def test_empty_second_third_product_is_null(session, tmp_path):
    _import(session, tmp_path, "us.csv", [
        _row(1, "solo product", a1="B001", t1="Only One", b1="X", cat1="Y"),  # 无 2nd/3rd
    ])
    row = session.execute(text(
        "SELECT top1_product_id, top2_product_id, top3_product_id FROM analysis.fact_daily")).one()
    assert row[0] is not None
    assert row[1] is None and row[2] is None


def test_is_new_true_when_no_previous_day(session, tmp_path):
    _import(session, tmp_path, "d1.csv", [_row(5, "kw", a1="B1", t1="T")])
    is_new = session.execute(text("SELECT is_new FROM analysis.fact_daily WHERE report_date='2026-07-15'")).scalar()
    assert is_new is True


def test_previous_day_drives_ranking_change(session, tmp_path):
    # 7/14 排名 100，7/15 排名 80 → change = -20，is_new=false
    _import(session, tmp_path, "d14.csv", [_row(100, "kw", a1="B1", t1="T")], d=date(2026, 7, 14))
    _import(session, tmp_path, "d15.csv", [_row(80, "kw", a1="B1", t1="T")], d=date(2026, 7, 15))
    row = session.execute(text("""
        SELECT current_ranking, previous_ranking, ranking_change, is_new
        FROM analysis.fact_daily WHERE report_date='2026-07-15'""")).one()
    assert row == (80, 100, -20, False)


def test_cross_site_same_keyword_coexist(session, tmp_path):
    _import(session, tmp_path, "us.csv", [_row(1, "wireless mouse", a1="B1", t1="T")], site="US")
    _import(session, tmp_path, "gb.csv", [_row(3, "wireless mouse", a1="B1", t1="T")], site="GB")
    counts = session.execute(text(
        "SELECT site, count(*) FROM analysis.fact_daily GROUP BY site ORDER BY site")).all()
    assert counts == [("GB", 1), ("US", 1)]
    # US 排名不因 GB 导入而变
    us_rank = session.execute(text("""
        SELECT f.current_ranking FROM analysis.fact_daily f
        JOIN analysis.dim_keyword k ON k.keyword_id=f.keyword_id
        WHERE f.site='US'""")).scalar()
    assert us_rank == 1


def test_reimport_same_day_overwrites(session, tmp_path):
    _import(session, tmp_path, "v1.csv", [_row(1, "a", a1="B1", t1="T"), _row(2, "b", a1="B2", t1="T")])
    _import(session, tmp_path, "v2.csv", [_row(9, "a", a1="B1", t1="T")])  # 重导，只剩 1 行
    n = session.execute(text("SELECT count(*) FROM analysis.fact_daily WHERE report_date='2026-07-15'")).scalar()
    assert n == 1
    rank = session.execute(text("SELECT current_ranking FROM analysis.fact_daily")).scalar()
    assert rank == 9


def test_bad_rows_and_empty_keyword_skipped_and_counted(session, tmp_path):
    good = _row(1, "good", a1="B1", t1="T")
    empty_kw = _row(2, "", a1="B2", t1="T")       # 搜索词空 → 跳过
    short = "1,onlytwo"                            # 列数不足 → 跳过
    r = _import(session, tmp_path, "mix.csv", [good, empty_kw, short])
    assert r.written_rows == 1
    assert r.skipped_rows == 2


def test_dirty_numeric_coerced_to_zero(session, tmp_path):
    # 排名 "abc" 应被 coerce 成 0，不报错（沿用 V1 容错）
    _import(session, tmp_path, "dirty.csv", [_row("abc", "kw", a1="B1", t1="T", cs1="N/A")])
    row = session.execute(text("SELECT current_ranking, top1_click_share FROM analysis.fact_daily")).one()
    assert row == (0, 0)


def test_weekly_uses_separate_table_and_prev_week(session, tmp_path):
    _import(session, tmp_path, "w8.csv", [_row(50, "kw", a1="B1", t1="T")], period="WEEK", d=date(2026, 7, 8))
    _import(session, tmp_path, "w15.csv", [_row(30, "kw", a1="B1", t1="T")], period="WEEK", d=date(2026, 7, 15))
    # 写进 fact_weekly，日报表为空
    assert session.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 0
    row = session.execute(text("""
        SELECT current_ranking, previous_ranking, ranking_change
        FROM analysis.fact_weekly WHERE report_date_week='2026-07-15'""")).one()
    assert row == (30, 50, -20)  # 上一"周"= 7/8


# --------------------------------------------------------------------------
# 品牌与类目是关键词级排行，不是商品属性
# --------------------------------------------------------------------------


def test_brands_and_categories_land_as_keyword_level_facts(session, tmp_path):
    """三个品牌 / 三个类目按名次写进事实表，各自关联到独立维度。

    这是本变更的核心：源报表里「点击量第 1/2/3 的品牌」与商品槽位无关，
    第 2 品牌可以属于一个根本没进前三商品的卖家。
    """
    _import(session, tmp_path, "kw_level.csv", [
        _row(1, "paper towels",
             b1="Bounty", b2="Charmin", b3="Sparkle",
             cat1="Health", cat2="Grocery", cat3="Home",
             a1="B001", t1="Bounty Rolls"),
    ])

    row = session.execute(text("""
        SELECT b1.name, b2.name, b3.name, c1.name, c2.name, c3.name
          FROM analysis.fact_daily f
          LEFT JOIN analysis.dim_brand b1 ON b1.brand_id = f.top1_brand_id
          LEFT JOIN analysis.dim_brand b2 ON b2.brand_id = f.top2_brand_id
          LEFT JOIN analysis.dim_brand b3 ON b3.brand_id = f.top3_brand_id
          LEFT JOIN analysis.dim_category c1 ON c1.category_id = f.top1_category_id
          LEFT JOIN analysis.dim_category c2 ON c2.category_id = f.top2_category_id
          LEFT JOIN analysis.dim_category c3 ON c3.category_id = f.top3_category_id
    """)).one()

    assert row == ("Bounty", "Charmin", "Sparkle", "Health", "Grocery", "Home")


def test_brand_ranks_are_independent_of_product_slots(session, tmp_path):
    """只有一个商品、却有三个品牌——旧模型下第 2/3 品牌无处安放，会被整个丢掉。

    这是「品牌不是商品属性」最直接的证据：品牌名次与商品名次的**数量都可以不一致**。
    """
    _import(session, tmp_path, "mismatch.csv", [
        _row(1, "kitchen towel",
             b1="Bounty", b2="Viva", b3="Brawny",
             cat1="Home",
             a1="B009", t1="Only Product"),  # 只有第 1 个商品槽位
    ])

    brands = session.execute(text("""
        SELECT b1.name, b2.name, b3.name
          FROM analysis.fact_daily f
          LEFT JOIN analysis.dim_brand b1 ON b1.brand_id = f.top1_brand_id
          LEFT JOIN analysis.dim_brand b2 ON b2.brand_id = f.top2_brand_id
          LEFT JOIN analysis.dim_brand b3 ON b3.brand_id = f.top3_brand_id
    """)).one()
    slots = session.execute(text(
        "SELECT top2_product_id, top3_product_id FROM analysis.fact_daily"
    )).one()

    assert brands == ("Bounty", "Viva", "Brawny")
    assert slots == (None, None), "商品槽位确实只有一个，但三个品牌都留住了"


def test_same_asin_under_different_keywords_keeps_own_brand_ranking(session, tmp_path):
    """同一 ASIN 在两个关键词下对应不同的品牌排行——旧模型下这正是 11.7% 冲突的来源。

    旧写法把品牌#1 当成商品#1 的属性写进 dim_product，两行冲突时 DISTINCT ON 任选其一
    胜出；现在各自记在自己的事实行上，互不影响。
    """
    _import(session, tmp_path, "same_asin.csv", [
        _row(1, "toilet paper", b1="Charmin", cat1="Health", a1="B777", t1="Shared Product"),
        _row(2, "bath tissue", b1="Cottonelle", cat1="Health", a1="B777", t1="Shared Product"),
    ])

    pairs = session.execute(text("""
        SELECT k.keyword, b.name
          FROM analysis.fact_daily f
          JOIN analysis.dim_keyword k ON k.keyword_id = f.keyword_id
          JOIN analysis.dim_brand b ON b.brand_id = f.top1_brand_id
         ORDER BY k.keyword
    """)).all()

    assert pairs == [("bath tissue", "Cottonelle"), ("toilet paper", "Charmin")]
    # 商品维度只此一份，且不再携带品牌
    assert session.execute(text(
        "SELECT count(*) FROM analysis.dim_product WHERE asin='B777'"
    )).scalar() == 1


def test_dim_product_no_longer_carries_brand_or_category(session, tmp_path):
    """缺陷的根：dim_product 不再被写入伪造属性。"""
    _import(session, tmp_path, "no_fake_attrs.csv", [
        _row(1, "mouse", b1="Logitech", cat1="Electronics", a1="B001", t1="Logi Mouse"),
    ])

    cols = session.execute(text("""
        SELECT count(*) FROM information_schema.columns
         WHERE table_schema='analysis' AND table_name='dim_product'
           AND column_name IN ('brand','category')
    """)).scalar()
    assert cols == 0


def test_missing_brand_rank_is_null_not_empty_string(session, tmp_path):
    """名次缺失时该位置为 NULL，不是指向一个空串品牌的引用。

    空串品牌会进 dim_brand 成为一个"名字是空的品牌"，然后出现在下拉里。
    """
    _import(session, tmp_path, "partial.csv", [
        _row(1, "single brand", b1="OnlyOne", cat1="Home", a1="B001", t1="P"),
    ])

    row = session.execute(text(
        "SELECT top2_brand_id, top3_brand_id, top2_category_id FROM analysis.fact_daily"
    )).one()
    assert row == (None, None, None)
    assert session.execute(text("SELECT count(*) FROM analysis.dim_brand WHERE name=''")).scalar() == 0
