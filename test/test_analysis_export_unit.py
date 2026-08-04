"""导出端点单元测试（无需 PostgreSQL）。

覆盖 migrate-frontend-to-antd-pro 任务 4.3 的后端半边：作用域内导出 CSV。
关注点：鉴权（analysis:export 而非 analysis:view）、CSV 形状（BOM/表头/按视图列集）、
超限拒绝（不静默截断）。数据库访问全部打桩，crud 的真实 SQL 由集成测试覆盖。
"""
import csv
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers
from app.table.analysis import analysis_api as api_mod
from app.table.analysis import analysis_service as service


class _StubUser:
    """够用即可：require_permission 只看 is_super 与 permission_codes。"""

    def __init__(self, *, is_super=False, permissions=()):
        self.is_super = is_super
        self.permission_codes = set(permissions)


def _row(kid: int, keyword: str, ranking: int, *, slots: int = 3) -> dict:
    """一行导出数据。``slots`` 控制有几个商品槽位有值，用来验证缺失槽位的呈现。"""
    row = {
        "keyword_id": kid,
        "keyword": keyword,
        "current_ranking": ranking,
        "previous_ranking": ranking + 3,
        "ranking_change": 3,
        "is_new": False,
    }
    for slot in range(1, slots + 1):
        row.update({
            f"top{slot}_asin": f"B0{kid:04d}{slot:04d}",
            f"top{slot}_title": f"Some Product {slot}",
            f"top{slot}_brand": f"BRAND{slot}",
            f"top{slot}_category": "Home",
            f"top{slot}_click_share": 25.0 / slot,
            f"top{slot}_conversion_share": 10.0 / slot,
        })
    return row


def _build_app(monkeypatch, *, user=None, rows=None, total=2, view_date=date(2026, 7, 18),
               trends=None):
    from app.auth.simple_auth import simple_auth
    from database import get_db

    rows = rows if rows is not None else [_row(1, "coffee maker", 1), _row(2, "kettle", 2)]

    monkeypatch.setattr(service.crud, "available_dates", lambda db, s, v: [view_date])
    monkeypatch.setattr(service.crud, "scope_row_count", lambda db, s, v, on: total)
    monkeypatch.setattr(service.crud, "count_capped", lambda db, s, v, on, req, cap: min(total, cap))
    monkeypatch.setattr(service.crud, "iter_export_rows",
                        lambda db, s, v, on, req, limit: iter([rows]))
    monkeypatch.setattr(service.crud, "fetch_trends", lambda db, s, ids, on, view: trends or {})
    # 流式生成器自建会话：测试里换成请求内的桩会话，避免真连库
    monkeypatch.setattr(service, "SessionFactory", lambda: _NullSession())

    app = FastAPI()
    # 必须挂上全局处理器：否则测试断言的是 FastAPI 默认形状，与生产行为脱节
    register_error_handlers(app)
    app.include_router(api_mod.analysis_router, prefix="/api/analysis")
    app.dependency_overrides[get_db] = lambda: None
    if user is not None:
        app.dependency_overrides[simple_auth.get_current_user] = lambda: user
    return app


class _NullSession:
    def close(self):
        pass


def _post(app, body=None):
    return TestClient(app).post("/api/analysis/export", json=body or {"site": "US", "view": "DAY"})


# ---------------- 鉴权 ----------------

def test_export_requires_auth(monkeypatch):
    """无 Authorization 头必拒——鉴权落在端点依赖，没有全局兜底。"""
    app = _build_app(monkeypatch)  # 不覆盖 get_current_user
    assert _post(app).status_code in (401, 403)


def test_export_requires_export_permission_not_just_view(monkeypatch):
    """只有 analysis:view 的用户不能导出：导出是独立权限点。"""
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:view"}))
    assert _post(app).status_code == 403


def test_export_allowed_with_export_permission(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(permissions={"analysis:export"}))
    assert _post(app).status_code == 200


# ---------------- CSV 形状 ----------------

def test_export_emits_bom_and_attachment_filename(monkeypatch):
    """BOM 让 Excel 正确识别 UTF-8 中文；文件名带作用域三元组便于归档。"""
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    r = _post(app)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "US_DAY_2026-07-18.csv" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")


def test_export_day_view_columns_and_rows(monkeypatch):
    """日视图列集含走势，不混入周变化；行数据按排序输出。"""
    app = _build_app(
        monkeypatch, user=_StubUser(is_super=True),
        trends={1: [5, 4, 3, 2, 1]},
    )
    body = _post(app).content.decode("utf-8-sig")
    lines = body.strip().splitlines()

    assert lines[0].startswith("关键词,")
    assert "近 7 日趋势" in lines[0] and "当周排名变化" not in lines[0]
    assert len(lines) == 3  # 表头 + 2 行
    assert "coffee maker" in lines[1]
    assert "5 4 3 2 1" in lines[1]
    # 缺走势的行留空，不补零
    assert lines[2].endswith(",")


def test_export_week_view_contains_week_trend(monkeypatch):
    """周视图导出按周回溯，表头和行值都不能冒充日趋势。"""
    app = _build_app(
        monkeypatch,
        user=_StubUser(is_super=True),
        trends={1: [30, 20, 10]},
    )
    r = _post(app, {"site": "US", "view": "WEEK"})
    lines = r.content.decode("utf-8-sig").splitlines()

    assert "近 7 周趋势" in lines[0] and "近 7 日趋势" not in lines[0]
    assert lines[1].endswith("30 20 10")
    assert "US_WEEK_2026-07-18.csv" in r.headers["content-disposition"]


def test_export_has_one_column_group_per_product_slot(monkeypatch):
    """三个商品槽位各一组列，表头能区分名次。

    界面上看得到前三、导出里只有第一名，会被当成缺陷——这是 show-top3-products
    定下的「导出即用户当下看到的那份数据」约定的具体化。
    """
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    header = _post(app).content.decode("utf-8-sig").splitlines()[0].split(",")

    for slot in (1, 2, 3):
        for field in ("ASIN", "商品标题", "点击份额", "转化份额"):
            assert f"Top{slot} {field}" in header
    # 名次顺序与卡牌一致：Top1 组整体在 Top2 组之前
    assert header.index("Top1 ASIN") < header.index("Top2 ASIN") < header.index("Top3 ASIN")


def test_export_separates_product_slots_from_brand_and_category_ranks(monkeypatch):
    """商品槽位列不含品牌与类目；品牌与类目各自按名次单独成列。

    两者是彼此独立的排行：源报表的商品槽位只有 ASIN/标题/两个份额，从来没有品牌与类目。
    此前导入侧把品牌#N 配给商品#N 存进 dim_product，同一 ASIN 携带冲突取值时任选其一
    胜出（实测品牌 11.7%、类目 3.4%，作为对照标题是 0%）——那样的值按名次导出等于把
    已知错误写进用户拿去分析的文件。现在品牌与类目有了自己的来源，可以按名次导出。
    """
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    header = _post(app).content.decode("utf-8-sig").splitlines()[0].split(",")

    # 商品槽位仍只有四类列
    for slot in (1, 2, 3):
        assert f"Top{slot} 品牌" not in header
        assert f"Top{slot} 类目" not in header
        assert f"Top{slot} ASIN" in header
    # 品牌与类目按名次各三列
    for rank in (1, 2, 3):
        assert f"品牌 Top{rank}" in header
        assert f"类目 Top{rank}" in header

def test_export_day_extra_column_stays_last(monkeypatch):
    """走势列在全部商品列之后——扩列不该把附加信息挤到中间。"""
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    header = _post(app).content.decode("utf-8-sig").splitlines()[0].split(",")

    assert header[-1] == "近 7 日趋势"


def test_export_missing_slot_is_blank_not_shifted(monkeypatch):
    """只有两个商品的行：第三组列留空，前两组仍是第一、第二名，不向后顺延。"""
    rows = [_row(1, "kettle", 1, slots=2)]
    app = _build_app(monkeypatch, user=_StubUser(is_super=True), rows=rows)
    body = _post(app).content.decode("utf-8-sig").splitlines()
    header, data = body[0].split(","), next(csv.reader([body[1]]))

    cell = dict(zip(header, data))
    assert cell["Top1 ASIN"] == "B000010001"
    assert cell["Top2 ASIN"] == "B000010002"
    assert cell["Top3 ASIN"] == ""
    assert cell["Top3 商品标题"] == ""
    assert cell["Top3 点击份额"] == ""


def test_export_renders_is_new_as_chinese(monkeypatch):
    rows = [dict(_row(1, "kettle", 1), is_new=True)]
    app = _build_app(monkeypatch, user=_StubUser(is_super=True), rows=rows)
    assert "是" in _post(app).content.decode("utf-8-sig").splitlines()[1]


# ---------------- 超限与空作用域 ----------------

def test_export_refuses_when_over_cap(monkeypatch):
    """超出上限拒绝导出，不静默截断——截断的 CSV 会被当成完整数据用。"""
    from config import settings

    app = _build_app(monkeypatch, user=_StubUser(is_super=True),
                     total=settings.EXPORT_ROW_CAP + 1)
    r = _post(app)

    assert r.status_code == 413
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "analysis.export_too_large"
    assert str(settings.EXPORT_ROW_CAP) in body["message"]
    assert "detail" not in body  # 统一契约，不再是 FastAPI 的 detail


def test_export_at_cap_is_allowed(monkeypatch):
    """恰好等于上限放行（边界不该误伤）。"""
    from config import settings

    app = _build_app(monkeypatch, user=_StubUser(is_super=True), total=settings.EXPORT_ROW_CAP)
    assert _post(app).status_code == 200


def test_export_empty_scope_returns_header_only(monkeypatch):
    """站点该视图无任何数据：返回只有表头的 CSV，而非报错。"""
    app = _build_app(monkeypatch, user=_StubUser(is_super=True), rows=[], total=0,
                     view_date=None)
    monkeypatch.setattr(service.crud, "available_dates", lambda db, s, v: [])
    r = _post(app)

    assert r.status_code == 200
    assert len(r.content.decode("utf-8-sig").strip().splitlines()) == 1


def test_export_rejects_unknown_site(monkeypatch):
    """非法站点报错不回退到默认站点（与 /search 的作用域纪律一致）。"""
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    r = _post(app, {"site": "UK", "view": "DAY"})  # 英国是 GB 不是 UK
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["code"] == "analysis.scope_invalid"
    assert "detail" not in body


# ---------------- 流式端点契约（任务组 4） ----------------

def test_over_cap_failure_emits_no_csv_bytes(monkeypatch):
    """开流前失败必须返回统一错误体，且一个 CSV 字节都不产出。

    一旦开流，状态码与响应头已发出，之后再失败只会得到一份被截断的 200 CSV，
    而它会被当成完整数据使用。
    """
    from config import settings

    app = _build_app(monkeypatch, user=_StubUser(is_super=True),
                     total=settings.EXPORT_ROW_CAP + 1)
    r = _post(app)

    assert r.status_code == 413
    assert not r.content.startswith(b"\xef\xbb\xbf")   # 无 BOM
    assert "关键词" not in r.text                       # 无表头
    assert r.headers["content-type"].startswith("application/json")
    assert "content-disposition" not in r.headers      # 未按附件下发


def test_scope_failure_emits_no_csv_bytes(monkeypatch):
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    r = _post(app, {"site": "UK", "view": "DAY"})

    assert r.status_code == 422
    assert "关键词" not in r.text
    assert "content-disposition" not in r.headers


def test_stream_interruption_is_logged_with_stack(monkeypatch, caplog):
    """开流后的异常无法转成错误响应，只能记日志断流——但必须带栈。"""
    import logging

    from app.table.analysis import analysis_service as svc

    def _boom(db, s, v, on, req, limit):
        yield [_row(1, "kettle", 1)]
        raise RuntimeError("cursor died mid-stream")

    # 必须在 _build_app 之后 patch —— 它内部也会设置 iter_export_rows
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    monkeypatch.setattr(svc.crud, "iter_export_rows", _boom)

    with caplog.at_level(logging.ERROR, logger="app.table.analysis.analysis_service"):
        try:
            _post(app)
        except RuntimeError:
            pass  # 开流后抛出时 TestClient 的行为随传输层而异，这里只关心是否留下线索

    record = next(r for r in caplog.records if "导出流中断" in r.getMessage())
    assert record.exc_info is not None
    assert record.levelno == logging.ERROR
