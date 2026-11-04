"""类目黑名单的真库验证（V1 迁入）。

默认跳过，只在设置 TEST_DATABASE_URL 时运行；测试库须已迁移。每个测试独占事务、结束回滚。
"""
import pytest
from sqlalchemy import text

from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from test.test_site_scoped_query_integration import (
    D3,
    SITE,
    TEST_URL,
    db,  # noqa: F401
)

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

# (关键词, top1 类目)；None 表示该词没有类目
ROWS = [
    ("kw-kept", "Electronics"),
    ("kw-ebook", "Digital_Ebook_Purchase"),
    ("kw-books", "BOOKS"),  # 库里大小写不同也要命中
    ("kw-nocat", None),
]


def _seed_categories(conn):
    for i, (kw, category) in enumerate(ROWS, start=1):
        cid = None
        if category is not None:
            cid = conn.execute(
                text("INSERT INTO analysis.dim_category(name) VALUES (:c) "
                     "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING category_id"),
                {"c": category},
            ).scalar()
        kid = conn.execute(
            text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": kw},
        ).scalar()
        conn.execute(
            text("INSERT INTO analysis.fact_daily"
                 "(site,report_date,keyword_id,current_ranking,top1_category_id) "
                 "VALUES (:s,:d,:k,:r,:c)"),
            {"s": SITE, "d": D3, "k": kid, "r": i, "c": cid},
        )
    # 预存总数故意写成 4（含黑名单行）：断言总数不受它影响
    conn.execute(
        text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
             "VALUES (:s,'DAY',:d,4)"),
        {"s": SITE, "d": D3},
    )
    conn.flush()


async def test_blacklisted_rows_are_excluded_but_null_category_is_kept(db):  # noqa: F811
    await db.run_sync(_seed_categories)
    resp = await service.search(
        db, AnalysisSearchRequest(site=SITE, view="DAY", date=D3)
    )
    assert sorted(r["keyword"] for r in resp.data) == ["kw-kept", "kw-nocat"]
    # 无筛选也实算，不读 site_data_stats 的预存 4
    assert resp.total == 2
