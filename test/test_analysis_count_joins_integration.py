"""计数只关联筛选用到的维度（#154 阶段二 D4）：改写前后的总数、与列表实际行数逐一相等。

改写前的计数总是内连接 ``dim_keyword``、左连接 top1 ``dim_product``；改写后按 ``_count_joins``
只在关键词检索 / 排除品牌词 / 首次入榜 / 标题筛选时关联。每个筛选组合都对照三件事：旧写法
计数、新写法计数、列表翻遍全部在榜行的条数。跑法同 ``test_site_scoped_query_integration.py``。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.table.analysis import analysis_crud as crud
from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from test import test_site_scoped_query_integration as _base
from test.test_site_scoped_query_integration import D1, D3, SITE, pytestmark  # noqa: F401

db = _base.db


def _seed(db):
    """D3 日报 5 行：一行第一类目在黑名单（列表与计数都不含），一行关键词含自己的第一品牌，
    两行首次入榜（监测起点 B = D1），一行存量词。"""
    def one(sql, **p):
        return db.execute(text(sql), p).scalar()

    acme = one("INSERT INTO analysis.dim_brand(name) VALUES ('acme') "
               "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING brand_id")
    tools = one("INSERT INTO analysis.dim_category(name) VALUES ('Tools') "
                "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING category_id")
    books = one("INSERT INTO analysis.dim_category(name) VALUES ('Books') "
                "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING category_id")
    drill = one("INSERT INTO analysis.dim_product(asin,title) VALUES ('B0CNTJOIN1','Acme Drill Pro') "
                "RETURNING product_id")
    saw = one("INSERT INTO analysis.dim_product(asin,title) VALUES ('B0CNTJOIN2','Hand Saw') "
              "RETURNING product_id")
    rows = [
        # keyword, basis, first_seen, rank, top1 product, top1 brand, top1 category
        ("acme drill", "IMPORTED", D3, 1, drill, acme, tools),
        ("hammer", "IMPORTED", D3, 2, saw, acme, tools),
        ("saw", "IMPORTED", D1, 3, saw, None, None),
        ("bench", "EARLIEST_OBSERVED", D1, 4, None, None, tools),
        ("novel", "IMPORTED", D3, 5, drill, acme, books),
    ]
    for keyword, basis, first_seen, rank, pid, bid, cid in rows:
        kid = one(
            "INSERT INTO analysis.dim_keyword(site,keyword,first_seen_date,first_seen_period_type,"
            "first_seen_basis) VALUES (:s,:k,:f,'DAY',:b) RETURNING keyword_id",
            s=SITE, k=keyword, f=first_seen, b=basis,
        )
        db.execute(text(
            "INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking,"
            "top1_product_id,top1_brand_id,top1_category_id) VALUES (:s,:d,:k,:r,:p,:b,:c)"
        ), {"s": SITE, "d": D3, "k": kid, "r": rank, "p": pid, "b": bid, "c": cid})
    db.execute(text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                    "VALUES (:s,'DAY',:d,5)"), {"s": SITE, "d": D3})
    db.flush()


COMBOS = [
    {},
    {"exclude_brand_in_keyword": True},
    {"first_listed": True},
    {"first_listed": True, "exclude_brand_in_keyword": True},
    {"keyword": "drill"},
    {"product_title": "drill"},
    {"keyword": "hammer", "product_title": "saw"},
    {"brands": ["acme"]},
    {"categories": ["Tools"]},
    {"asins": ["B0CNTJOIN2"]},
    {"ranking_max": 3},
]


async def _old_count(db, req):
    stmt, fact = crud._count_base_query(SITE, "DAY", D3, keyword=True, product=True)
    stmt = crud._apply_filters(stmt, fact, req, site=SITE, view="DAY", on=D3)
    return (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()


@pytest.mark.parametrize("extra", COMBOS, ids=lambda e: ",".join(e) or "default")
async def test_count_matches_old_joins_and_listed_rows(db, extra):
    await db.run_sync(lambda s: _seed(s))
    req = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=200, include="LISTED", **extra)

    new = (await db.execute(crud.count_filtered_stmt(SITE, "DAY", D3, req))).scalar_one()
    old = await _old_count(db, req)
    resp = await service.search(db, req)

    assert new == old
    assert resp.total == new == len(resp.data)


async def test_seed_actually_exercises_each_filter(db):
    """守卫：上面每个组合都要筛掉点什么，否则「新旧相等」可能只是都没生效。"""
    await db.run_sync(lambda s: _seed(s))
    base = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, include="LISTED")
    total = (await db.execute(crud.count_filtered_stmt(SITE, "DAY", D3, base))).scalar_one()
    assert total == 4  # 黑名单类目那行不计
    for extra in COMBOS[1:]:
        req = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, include="LISTED", **extra)
        n = (await db.execute(crud.count_filtered_stmt(SITE, "DAY", D3, req))).scalar_one()
        assert 0 < n < total, extra


async def test_first_listed_count_matches_filtered_total(db):
    await db.run_sync(lambda s: _seed(s))
    req = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, first_listed=True)
    filtered = (await db.execute(crud.count_filtered_stmt(SITE, "DAY", D3, req))).scalar_one()
    summary = (await db.execute(crud.first_listed_count_stmt(SITE, "DAY", D3))).scalar_one()
    assert summary == filtered == 2  # acme drill、hammer；novel 在黑名单类目
