"""排除品牌词的预算命中对（#154 阶段二 D4 第二步）：就绪切片的结果与逐行判定逐一相等。

覆盖判定的边角：正则元字符、过短品牌名、第二/三槽位、子串但非整词、大小写、空槽位；以及导入
收尾置就绪、重导先置假再置真、回填脚本只处理未就绪切片。跑法同 ``test_site_scoped_query_integration.py``。
"""

from __future__ import annotations

from sqlalchemy import text

from app.table.analysis import analysis_crud as crud
from app.table.analysis import analysis_service as service
from app.table.analysis.brand_hits import invalidate_slice, rebuild_slice
from app.table.search.search_schemas import AnalysisSearchRequest
from test import test_site_scoped_query_integration as _base
from test.test_site_scoped_query_integration import D3, SITE, USER_ID, pytestmark  # noqa: F401

db = _base.db

# 关键词, (top1, top2, top3 品牌), 逐行判定下是否被排除
CASES = [
    ("acme drill", ("acme", None, None), True),
    ("ACME Drill", ("acme", None, None), True),           # 不分大小写
    ("acmex drill", ("acme", None, None), False),         # 子串但不是整词
    ("drill set", ("bosch", "acme", None), False),
    ("bosch drill", ("makita", "acme", "bosch"), True),   # 第三槽位
    ("a.b tool", ("a.b", None, None), True),              # 品牌名含正则元字符
    ("axb tool", ("a.b", None, None), False),             # 元字符必须按字面
    ("a tool", ("a", None, None), False),                 # 品牌名过短不参与
    ("no brand", (None, None, None), False),
]


def _seed(db):
    def one(sql, **p):
        return db.execute(text(sql), p).scalar()

    brand_ids: dict[str, int] = {}
    for name in {b for _, slots, _ in CASES for b in slots if b}:
        brand_ids[name] = one("INSERT INTO analysis.dim_brand(name) VALUES (:n) "
                              "ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING brand_id", n=name)
    for rank, (keyword, slots, _) in enumerate(CASES, start=1):
        kid = one("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id",
                  s=SITE, k=keyword)
        b1, b2, b3 = (brand_ids.get(b) if b else None for b in slots)
        db.execute(text(
            "INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking,"
            "top1_brand_id,top2_brand_id,top3_brand_id) VALUES (:s,:d,:k,:r,:b1,:b2,:b3)"
        ), {"s": SITE, "d": D3, "k": kid, "r": rank, "b1": b1, "b2": b2, "b3": b3})
    db.execute(text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) "
                    "VALUES (:s,'DAY',:d,:n)"), {"s": SITE, "d": D3, "n": len(CASES)})
    db.flush()


def _ready(db_sync) -> bool:
    return db_sync.execute(crud.brand_hits_ready_stmt(SITE, "DAY", D3)).scalar_one()


async def _keywords(db, **extra) -> tuple[int, list[str]]:
    req = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, perPage=200, include="LISTED", **extra)
    resp = await service.search(db, req, user_id=USER_ID)
    return resp.total, sorted(r["keyword"] for r in resp.data)


async def test_ready_slice_excludes_exactly_what_row_by_row_excludes(db):
    await db.run_sync(_seed)
    expected = sorted(k for k, _, excluded in CASES if not excluded)

    assert not await db.run_sync(_ready)
    by_row = await _keywords(db, exclude_brand_in_keyword=True)
    assert by_row == (len(expected), expected)

    added = await db.run_sync(lambda s: rebuild_slice(s, site=SITE, period="DAY", report_date=D3))
    assert added == sum(1 for *_, excluded in CASES if excluded)
    assert await db.run_sync(_ready)
    assert await _keywords(db, exclude_brand_in_keyword=True) == by_row


async def test_ready_path_drops_the_keyword_text_join(db):
    """就绪时排除品牌词的计数不再需要关键词表——这正是它快的原因。"""
    req = AnalysisSearchRequest(site=SITE, view="DAY", date=D3, exclude_brand_in_keyword=True)
    assert crud._count_joins(req, brand_hits_ready=True)["keyword"] is False
    assert crud._count_joins(req, brand_hits_ready=False)["keyword"] is True


async def test_ready_matches_row_by_row_combined_with_other_filters(db):
    await db.run_sync(_seed)
    combos = [{"keyword": "drill"}, {"ranking_max": 5}, {"brands": ["acme"]}]
    before = [await _keywords(db, exclude_brand_in_keyword=True, **c) for c in combos]
    await db.run_sync(lambda s: rebuild_slice(s, site=SITE, period="DAY", report_date=D3))
    after = [await _keywords(db, exclude_brand_in_keyword=True, **c) for c in combos]
    assert after == before
    assert all(0 < total for total, _ in after)


async def test_rebuild_is_idempotent(db):
    await db.run_sync(_seed)
    await db.run_sync(lambda s: rebuild_slice(s, site=SITE, period="DAY", report_date=D3))
    again = await db.run_sync(lambda s: rebuild_slice(s, site=SITE, period="DAY", report_date=D3))
    assert again == 0


async def test_invalidate_falls_back_to_row_by_row(db):
    """切片被重写时先置假：新出现的对还没判定，不能沿用旧的就绪。"""
    await db.run_sync(_seed)
    await db.run_sync(lambda s: rebuild_slice(s, site=SITE, period="DAY", report_date=D3))
    await db.run_sync(lambda s: invalidate_slice(s, site=SITE, period="DAY", report_date=D3))
    assert not await db.run_sync(_ready)
    # 新的一行：命中表里没有这一对，逐行判定照样把它排除
    await db.run_sync(lambda s: s.execute(text(
        "WITH k AS (INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,'bosch saw') "
        "           RETURNING keyword_id) "
        "INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking,top1_brand_id) "
        "SELECT :s, :d, k.keyword_id, 99, b.brand_id FROM k, analysis.dim_brand b WHERE b.name = 'bosch'"
    ), {"s": SITE, "d": D3}))
    total, keywords = await _keywords(db, exclude_brand_in_keyword=True)
    assert "bosch saw" not in keywords


def test_import_finish_marks_slice_ready_and_reimport_resets(tmp_path):
    """真实导入：收尾置就绪；同一期重导的事实事务里先置假，收尾后再置真。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    from test.test_import_service_integration import TEST_URL, _row, _write_csv

    engine = create_engine(TEST_URL)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    d = D3
    try:
        for name, kw in (("a.csv", "acme drill"), ("b.csv", "bosch drill")):
            path = _write_csv(tmp_path, name, [_row(1, kw, b1=kw.split()[0])])
            batch = run_import(site=SITE, period="DAY", report_date=d, source=LocalFileSource(path),
                               original_filename=name, session_factory=sf)
            assert batch.status == "COMPLETED"
            with sf() as s:
                assert s.execute(crud.brand_hits_ready_stmt(SITE, "DAY", d)).scalar_one() is True
                hit = s.execute(text(
                    "SELECT count(*) FROM analysis.keyword_brand_hit h "
                    "JOIN analysis.dim_keyword k USING (keyword_id) WHERE k.keyword = :k"
                ), {"k": kw}).scalar_one()
                assert hit == 1, kw
    finally:
        with engine.begin() as c:
            c.execute(text("TRUNCATE analysis.fact_daily, analysis.fact_weekly, analysis.fact_dropped, "
                           "analysis.dim_keyword, analysis.dim_product, analysis.dim_brand, "
                           "analysis.keyword_brand_hit, analysis.site_data_stats, "
                           "analysis.import_batch RESTART IDENTITY CASCADE"))
        engine.dispose()


def test_backfill_script_only_touches_pending_slices(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from scripts import backfill_brand_hits as bf
    from test.test_import_service_integration import TEST_URL

    engine = create_engine(TEST_URL)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sf() as s:
            _seed(s)
            s.commit()
            assert bf.pending_slices(s, site=SITE, view="DAY", limit=None) == [(SITE, "DAY", D3)]
        monkeypatch.setattr("database.SessionFactory", sf)
        monkeypatch.setattr("database.engine", engine)
        assert bf.main(["--site", SITE, "--view", "DAY", "--yes"]) == 0
        with sf() as s:
            assert bf.pending_slices(s, site=SITE, view="DAY", limit=None) == []
            assert _ready(s) is True
        assert bf.main(["--site", SITE, "--yes"]) == 0  # 没有待处理切片，直接结束
    finally:
        with engine.begin() as c:
            c.execute(text("TRUNCATE analysis.fact_daily, analysis.dim_keyword, analysis.dim_brand, "
                           "analysis.keyword_brand_hit, analysis.site_data_stats RESTART IDENTITY CASCADE"))
        engine.dispose()


def test_fact_rewrite_alone_invalidates_ready_slice(tmp_path):
    """只跑事实写入（不收尾）：已就绪的切片被同一事务置假——收尾重算前查询不会沿用旧就绪。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.table.upload.import_source import LocalFileSource
    from app.table.upload.importer import import_report
    from test.test_import_service_integration import TEST_URL, _row, _write_csv

    engine = create_engine(TEST_URL)
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn)
    try:
        s.execute(text("INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count,"
                       "brand_hits_ready) VALUES (:s,'DAY',:d,1,true)"), {"s": SITE, "d": D3})
        path = _write_csv(tmp_path, "r.csv", [_row(1, "acme drill", b1="acme")])
        import_report(s, site=SITE, period="DAY", report_date=D3, source=LocalFileSource(path))
        assert s.execute(crud.brand_hits_ready_stmt(SITE, "DAY", D3)).scalar_one() is False
    finally:
        s.close()
        trans.rollback()
        conn.close()
        engine.dispose()
