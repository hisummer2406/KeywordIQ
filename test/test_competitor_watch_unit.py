"""竞对监控查询层的单元测试（不连库）。

覆盖不需要数据库就能钉死的事：请求 schema 白名单/边界、去重与优先级折算的纯逻辑、
upsert 语句按 append/replace_group 模式生成不同的 SET 子句。配额判定、幂等、跨用户
隔离要真的写库才有意义，在 test_competitor_watch_integration.py（TEST_DATABASE_URL gate）。
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from app.table.competitor import watch_crud as crud
from app.table.competitor import watch_schemas
from app.table.competitor.seller_ref_parser import ParsedSellerRef
from app.table.competitor.watch_schemas import (
    CommitRequest,
    GroupCreateRequest,
    WatchListRequest,
    WatchRemoveRequest,
)
from app.table.competitor.watch_service import _dedup_entries

# ---------- 请求 schema 白名单 ----------


def test_建组名不能为空白():
    with pytest.raises(ValidationError):
        GroupCreateRequest(name="")


def test_建组名超长被拒():
    with pytest.raises(ValidationError):
        GroupCreateRequest(name="x" * 51)


def test_commit模式只接受两个字面量():
    with pytest.raises(ValidationError):
        CommitRequest(preview_token="t", mode="overwrite_all")  # type: ignore[arg-type]


def test_watchlist分页参数有边界():
    with pytest.raises(ValidationError):
        WatchListRequest(perPage=0)
    with pytest.raises(ValidationError):
        WatchListRequest(perPage=201)
    with pytest.raises(ValidationError):
        WatchListRequest(page=0)


def test_批量移除条数上限():
    with pytest.raises(ValidationError):
        WatchRemoveRequest(watch_ids=[])
    with pytest.raises(ValidationError):
        WatchRemoveRequest(watch_ids=list(range(501)))


# ---------- 去重与优先级折算 ----------


def test_去重按seller_id与site联合键():
    parsed = [
        ParsedSellerRef(seller_id="A1", site="US"),
        ParsedSellerRef(seller_id="A1", site="US"),
        ParsedSellerRef(seller_id="A1", site="GB"),  # 同 ID 不同站点，算两个不同主体
    ]
    entries = _dedup_entries(parsed, [None, None, None])
    assert len(entries) == 2
    by_site = {e["site"]: e for e in entries}
    assert by_site["US"]["priority"] == 2
    assert by_site["GB"]["priority"] == 1


def test_分组名取首次出现的非空值():
    parsed = [ParsedSellerRef(seller_id="A1", site="US")] * 2
    entries = _dedup_entries(parsed, [None, "竞品店铺"])
    assert entries[0]["group_name"] == "竞品店铺"


def test_状态只有三态_没有已暂停():
    """#166：未采集 / 已采集 / 数据陈旧，由服务端在 SQL 里判定（见集成测试）。
    PAUSED 被删掉了，别再加回来当占位取值。

    它曾作为「后续数据获取层」的预留取值存在，但没有任何字段或端点能让一行进入该状态
    ——一个永远取不到值的状态会让读界面的人以为系统能暂停某个卖家。要做暂停，它属于
    私有层 user_seller_watch，是另一次变更。
    """
    assert set(get_args(watch_schemas.WatchStatus)) == {"NOT_COLLECTED", "COLLECTED", "STALE"}


# ---------- upsert 语句形状 ----------


def test_append模式set子句只含priority():
    rows = [{"user_id": 1, "seller_id": "A1", "site": "US", "group_id": None,
             "priority": 1, "note": None}]
    stmt = crud.upsert_watches_stmt(rows, overwrite_group=False)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "priority" in compiled
    assert "group_id = excluded.group_id" not in compiled.lower().replace(" ", "")
    assert "note = excluded.note" not in compiled.lower().replace(" ", "")


def test_replace_group模式set子句含group_id与note():
    rows = [{"user_id": 1, "seller_id": "A1", "site": "US", "group_id": 9,
             "priority": 1, "note": None}]
    stmt = crud.upsert_watches_stmt(rows, overwrite_group=True)
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False})).lower().replace(" ", "")
    assert "group_id=excluded.group_id" in compiled
    assert "note=excluded.note" in compiled


# ---------- ASIN 详情响应契约（PR #75 审核 P2） ----------
#
# 前端 services/competitor.ts 把 AsinDetailResponse.success 声明为必填，但后端一度
# 没有这个字段——真实 JSON 不会返回它。前端 mock 都手写了 success: true，把契约
# 不一致掩盖了下来，只有真正走一遍序列化才能发现。用 TestClient 打真实的 HTTP 层
# （而不是直接调 service 函数），这样才能验证 FastAPI 的 response_model 序列化
# 结果，不是绕过它直接读 Python 对象。


def test_ASIN详情响应体含success字段():
    from datetime import UTC, datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.simple_auth import simple_auth
    from app.common.error_handlers import register_error_handlers
    from app.table.competitor import watch_api as api_mod
    from app.table.competitor.watch_schemas import AsinDetailResponse
    from database import get_async_db

    class _StubUser:
        is_super = True
        permission_codes: set[str] = set()
        id = 1

    async def _fake_asin_detail(*_args, **_kwargs):
        return AsinDetailResponse(
            first_seen_at=datetime(2026, 8, 2, tzinfo=UTC),
            seller_last_reported_at=None,
        )

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(api_mod.service, "asin_detail", _fake_asin_detail)
    try:
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(api_mod.competitor_router, prefix="/api/competitor")
        app.dependency_overrides[get_async_db] = lambda: None
        app.dependency_overrides[simple_auth.get_current_user] = lambda: _StubUser()

        r = TestClient(app).get("/api/competitor/watches/A1/US/B0X/detail")

        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["first_seen_at"] is not None
    finally:
        monkeypatch.undo()
