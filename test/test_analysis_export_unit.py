"""导出端点单元测试（无需 PostgreSQL）。

覆盖 migrate-frontend-to-antd-pro 任务 4.3 的后端半边：作用域内导出 CSV。
关注点：鉴权（analysis:export 而非 analysis:view）、CSV 形状（BOM/表头/按视图列集）、
超限拒绝（不静默截断）。数据库访问全部打桩，crud 的真实 SQL 由集成测试覆盖。
"""
from datetime import date

import pytest
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


def _row(kid: int, keyword: str, ranking: int) -> dict:
    return {
        "keyword_id": kid,
        "keyword": keyword,
        "current_ranking": ranking,
        "previous_ranking": ranking + 3,
        "ranking_change": 3,
        "is_new": False,
        "top1_asin": f"B0{kid:08d}",
        "top1_title": "Some Product",
        "top1_brand": "ACME",
        "top1_category": "Home",
        "top1_click_share": 0.25,
        "top1_conversion_share": 0.1,
    }


def _build_app(monkeypatch, *, user=None, rows=None, total=2, view_date=date(2026, 7, 18),
               trends=None, week_changes=None):
    from database import get_db
    from app.auth.simple_auth import simple_auth

    rows = rows if rows is not None else [_row(1, "coffee maker", 1), _row(2, "kettle", 2)]

    monkeypatch.setattr(service.crud, "available_dates", lambda db, s, v: [view_date])
    monkeypatch.setattr(service.crud, "scope_row_count", lambda db, s, v, on: total)
    monkeypatch.setattr(service.crud, "count_capped", lambda db, s, v, on, req, cap: min(total, cap))
    monkeypatch.setattr(service.crud, "iter_export_rows",
                        lambda db, s, v, on, req, limit: iter([rows]))
    monkeypatch.setattr(service.crud, "fetch_trends", lambda db, s, ids, on: trends or {})
    monkeypatch.setattr(service.crud, "fetch_week_changes", lambda db, s, ids, on: week_changes or {})
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
    """日视图列集含走势与当周变化；行数据按排序输出。"""
    app = _build_app(
        monkeypatch, user=_StubUser(is_super=True),
        trends={1: [5, 4, 3, 2, 1]}, week_changes={1: -7},
    )
    body = _post(app).content.decode("utf-8-sig")
    lines = body.strip().splitlines()

    assert lines[0].startswith("关键词,")
    assert "近7天走势" in lines[0] and "当周排名变化" in lines[0]
    assert len(lines) == 3  # 表头 + 2 行
    assert "coffee maker" in lines[1]
    assert "5 4 3 2 1" in lines[1]
    assert lines[1].endswith("-7")
    # 缺走势/周变化的行留空，不补零
    assert lines[2].endswith(",,")


def test_export_week_view_omits_day_only_columns(monkeypatch):
    """周视图没有走势/当周变化这两列——它们是日视图的附加信息。"""
    app = _build_app(monkeypatch, user=_StubUser(is_super=True))
    r = _post(app, {"site": "US", "view": "WEEK"})
    header = r.content.decode("utf-8-sig").splitlines()[0]

    assert "近7天走势" not in header and "当周排名变化" not in header
    assert "US_WEEK_2026-07-18.csv" in r.headers["content-disposition"]


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
