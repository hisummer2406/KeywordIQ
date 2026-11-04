"""导出链路集成测试：验证单元测试打桩掉的那部分——真实 SQL。

默认跳过，只在设置 TEST_DATABASE_URL 时运行（沿用 V2 集成测试的 gate 模式）。
复用 site-scoped-query 集成测试的受控样本（K1 跨 D1/D2/D3 + 同周周报，K2 只在 D3）。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_analysis_export_integration.py -v
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.table.analysis import analysis_crud as crud
from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from test.test_site_scoped_query_integration import (
    D2,
    D3,
    SITE,
    _ensure_partitions_sync,
    _seed,
    _seed_top_slots,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


@pytest.fixture()
def db():
    # 分区必须自己确保，不能指望别的测试模块建过。
    #
    # 这里踩过一个只在**全新库**上才现形的坑：分区是提交的 DDL（刻意如此，见
    # _ensure_partitions_sync 的说明），而本文件按字母序排在 site_scoped 之前。本文件
    # 曾不建分区，于是全新库上第一次跑必挂 9 个；site_scoped 随后把分区建好并提交，
    # 第二次起就再也复现不了——CI 上表现为「首次运行红、重跑绿」，最难归因的那种。
    if not _ensure_partitions_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")

    engine = create_engine(TEST_URL)
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()
        engine.dispose()


def _req(**kw) -> AnalysisSearchRequest:
    return AnalysisSearchRequest(site=SITE, view="DAY", date=D3, **kw)


def _bind_stream_to_fixture(monkeypatch, db):
    """让 iter_export_csv 的自建会话落在夹具那条连接上。

    ``iter_export_csv`` 按设计自建会话（响应体在依赖清理之后才被消费，复用请求会话会踩
    「会话已关闭」）——但那个 ``SessionFactory`` 绑的是 .env 里的**应用库**，不是测试库。
    不接管的话，这条路径在测试里查的是另一个库：夹具灌的数据一行也看不见，导出产出
    只有表头，而针对表头的断言照样通过。**本文件原有的几个 iter_export_csv 用例就是
    这样在空结果上「通过」的**，我加掉榜行的用例时才暴露出来。

    绑到夹具的连接上而不是新建引擎：夹具的数据在未提交的事务里，另开连接看不见它。
    """
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(service, "SessionFactory", sessionmaker(bind=db.get_bind()))


def test_iter_export_rows_streams_scoped_rows(db):
    """服务端游标分块产出：块内是 dict，作用域与排序与列表一致。"""
    _seed(db)
    chunks = list(crud.iter_export_rows(db, SITE, "DAY", D3, _req(), limit=100))

    rows = [r for chunk in chunks for r in chunk]
    assert [r["current_ranking"] for r in rows] == [1, 9]  # 默认按当期排名升序
    assert {r["keyword"] for r in rows} == {"kw-one", "kw-two"}


def test_iter_export_rows_respects_limit(db):
    """limit 封顶：上限之外的行不会被取回。"""
    _seed(db)
    rows = [
        r
        for chunk in crud.iter_export_rows(db, SITE, "DAY", D3, _req(), limit=1)
        for r in chunk
    ]
    assert len(rows) == 1


def test_iter_export_rows_applies_filters(db):
    """筛选与列表同源：只导出筛出来的那部分。"""
    _seed(db)
    rows = [
        r
        for chunk in crud.iter_export_rows(
            db, SITE, "DAY", D3, _req(keyword="kw-two"), limit=100
        )
        for r in chunk
    ]
    assert [r["keyword"] for r in rows] == ["kw-two"]


def test_export_asin_batch_matches_search_rows(db):
    """ASIN 批量筛选在导出与精确计数上与列表同口径（#150）：同一关键词多槽位命中只导出一行。"""
    _seed(db)
    kids = _seed_top_slots(db)
    req = _req(asins=["B0SLOT00001", "B0SLOT00003", "B0NOTEXIST"])

    rows = [
        r
        for chunk in crud.iter_export_rows(db, SITE, "DAY", D3, req, limit=100)
        for r in chunk
    ]
    assert sorted(r["keyword_id"] for r in rows) == sorted(kids.values())
    counted = db.execute(crud.count_filtered_stmt(SITE, "DAY", D3, req)).scalar_one()
    assert counted == len(rows) == 2


def test_count_capped_stops_at_cap(db):
    """上限计数数满即止，不给出真实总数。"""
    _seed(db)
    assert crud.count_capped(db, SITE, "DAY", D3, _req(), cap=1) == 1
    assert crud.count_capped(db, SITE, "DAY", D3, _req(), cap=10) == 2


def test_export_csv_contains_day_extras(db, monkeypatch):
    """端到端 CSV：日视图带走势，且走势是真实跨天回溯的结果。

    当周排名变化已随 80f83df 从日视图移除（它把周报数据混进了日报视图），故走势是
    日视图现存的唯一附加列，位置在全部商品列之后——也就是行尾。

    这里连表头与行尾一起断言：只查表头的话，多出来一列空值不会被发现（表头对了、
    行尾多个逗号，肉眼与断言都容易放过）。
    """
    _seed(db)
    monkeypatch.setattr(service, "SessionFactory", lambda: db)

    site, view, on = service.resolve_export_scope(db, _req())
    body = "".join(service.iter_export_csv(site, view, on, _req()))
    lines = body.lstrip("﻿").strip().splitlines()

    assert "近 7 日趋势" in lines[0] and "当周排名变化" not in lines[0]
    assert len(lines) == 3
    kw_one = next(line for line in lines[1:] if "kw-one" in line)
    assert kw_one.endswith("5 3 1")  # D1→D2→D3 的排名回溯，且走势就是最后一列


def test_export_week_view_contains_real_week_trend(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(service, "SessionFactory", lambda: db)

    req = AnalysisSearchRequest(site=SITE, view="WEEK")
    site, view, on = service.resolve_export_scope(db, req)
    lines = "".join(service.iter_export_csv(site, view, on, req)).lstrip("﻿").splitlines()

    assert "近 7 周趋势" in lines[0] and "近 7 日趋势" not in lines[0]
    assert lines[1].endswith("9 5 1")


def test_export_scope_rejects_over_cap_unfiltered(db, monkeypatch):
    """无筛选时上限检查读 site_data_stats（D3 共 2 行）。"""
    _seed(db)
    monkeypatch.setattr(service.settings, "EXPORT_ROW_CAP", 1)

    with pytest.raises(service.ExportTooLargeError):
        service.resolve_export_scope(db, _req())


def test_export_scope_rejects_over_cap_filtered(db, monkeypatch):
    """有筛选时走上限计数，同样拒绝而非截断。"""
    _seed(db)
    monkeypatch.setattr(service.settings, "EXPORT_ROW_CAP", 0)

    with pytest.raises(service.ExportTooLargeError):
        service.resolve_export_scope(db, _req(keyword="kw"))


def test_export_scope_allows_within_cap(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(service.settings, "EXPORT_ROW_CAP", 2)
    assert service.resolve_export_scope(db, _req()) == (SITE, "DAY", D3)


# --------------------------------------------------------------------------
# 掉榜行：导出必须与屏幕上那份行集合一致（任务 3.9）
# --------------------------------------------------------------------------


def _seed_dropped(db, keyword: str = "kw-gone", last_ranking: int = 42):
    """在 D3 这一期造一个掉榜词：窗口内出现过，锚定期没有行。"""
    kid = db.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:kw) RETURNING keyword_id"),
        {"s": SITE, "kw": keyword},
    ).scalar()
    db.execute(
        text("INSERT INTO analysis.fact_dropped"
             "(site,period_type,anchor_date,keyword_id,last_seen_date,last_ranking) "
             "VALUES (:s,'DAY',:a,:k,:l,:r)"),
        {"s": SITE, "a": D3, "k": kid, "l": D2, "r": last_ranking},
    )
    # 预存计数与事实行一起维护——生产上由导入收尾同时写这两处。只写事实行的话，
    # 分页分界线与导出上限读到的仍是 0，测出来的是一个现实中不存在的状态。
    db.execute(
        text("UPDATE analysis.site_data_stats SET dropped_count = dropped_count + 1 "
             "WHERE site=:s AND period_type='DAY' AND report_date=:a"),
        {"s": SITE, "a": D3},
    )
    return kid


def test_export_includes_dropped_rows(db, monkeypatch):
    """导出的就是用户当下看到的那份数据——掉榜行也在其中。

    列表已经按「在榜 ∪ 掉榜」返回，导出若只导在榜段，用户拿到的 CSV 会比屏幕上少一批
    行，且行数对不上——而这恰恰是掉榜功能最想让人拿去分析的那批词。
    """
    _seed(db)
    _seed_dropped(db)
    _bind_stream_to_fixture(monkeypatch, db)

    site, view, on = service.resolve_export_scope(db, _req())
    body = "".join(service.iter_export_csv(site, view, on, _req()))

    assert "kw-gone" in body, "导出漏掉了掉榜行"


def test_export_cap_counts_both_segments(db, monkeypatch):
    """上限要按两段之和判定，且必须在开流之前判完。

    只数在榜段的话，两段合计超限的导出会照常开流——而流一旦开始就只能给出 200 加一份
    被截断的 CSV，截断的 CSV 会被当成完整数据用。
    """
    _seed(db)          # D3 在榜 2 行
    _seed_dropped(db)  # D3 掉榜 1 行 → 合计 3 行

    monkeypatch.setattr(service.settings, "EXPORT_ROW_CAP", 2)
    with pytest.raises(service.ExportTooLargeError):
        service.resolve_export_scope(db, _req())


def test_export_respects_include_listed(db, monkeypatch):
    """include=LISTED 时导出不含掉榜行——导出与列表用同一个行集合开关。"""
    _seed(db)
    _seed_dropped(db)
    _bind_stream_to_fixture(monkeypatch, db)

    req = _req(include="LISTED")
    site, view, on = service.resolve_export_scope(db, req)
    body = "".join(service.iter_export_csv(site, view, on, req))

    assert "kw-gone" not in body
    assert "kw-one" in body
