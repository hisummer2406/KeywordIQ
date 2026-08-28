"""竞对监控查询层的单元测试（不连库）。

覆盖不需要数据库就能钉死的事：请求 schema 白名单/边界、去重与优先级折算的纯逻辑、
upsert 语句按 append/replace_group 模式生成不同的 SET 子句。配额判定、幂等、跨用户
隔离要真的写库才有意义，在 test_competitor_watch_integration.py（TEST_DATABASE_URL gate）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.table.competitor import watch_crud as crud
from app.table.competitor.seller_ref_parser import ParsedSellerRef
from app.table.competitor.watch_schemas import (
    CommitRequest,
    GroupCreateRequest,
    WatchListRequest,
    WatchRemoveRequest,
)
from app.table.competitor.watch_service import _dedup_entries, _status_of

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


def test_未标注状态恒为待建立基线():
    assert _status_of(None) == "PENDING_BASELINE"


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
