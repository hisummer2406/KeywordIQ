"""类目黑名单（V1 迁入）的单元测试，无需 PostgreSQL。

真库行为（行、总数、导出实际被排除，NULL 类目保留）见 test_hidden_categories_integration.py。
"""
from app.table.analysis import analysis_crud as crud
from app.table.search.search_schemas import AnalysisSearchRequest

V1_BLACKLIST = {
    "books",
    "grocery",
    "video games",
    "digital_video_download",
    "digital_ebook_purchase",
    "digital_music_purchase",
}


def _req(**kw) -> AnalysisSearchRequest:
    return AnalysisSearchRequest(site="US", view="DAY", **kw)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": False})).lower()


def test_blacklist_covers_the_v1_categories():
    assert crud.HIDDEN_CATEGORY_NAMES == V1_BLACKLIST
    # 比较前对库里的名字取小写，名单里写大写永远匹配不上，且不会报错
    assert all(name == name.lower() for name in crud.HIDDEN_CATEGORY_NAMES)


def test_every_query_path_excludes_the_blacklist():
    """表格、总数、导出上限检查、导出共用 base query；漏掉任一条就是表格与导出行数对不上。"""
    from datetime import date

    on = date(2026, 7, 18)
    req = _req()
    stmts = {
        "search": crud.search_stmt("US", "DAY", on, req, user_id=1),
        "count_filtered": crud.count_filtered_stmt("US", "DAY", on, req, 1),
        "count_capped": crud.count_capped_stmt("US", "DAY", on, req, 10, 1),
        "export": crud.export_stmt("US", "DAY", on, req, 10, 1),
    }
    for name, stmt in stmts.items():
        sql = _sql(stmt)
        assert "top1_category_id is null" in sql, name
        assert "lower(analysis.dim_category.name)" in sql, name


def test_blacklist_does_not_count_as_a_user_filter():
    """它是恒定的基线条件，不是用户筛选：计入的话掉榜段会在默认视图里整体消失。"""
    assert crud.request_has_filters(_req()) is False
