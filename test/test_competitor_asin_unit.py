"""竞对 ASIN 上报的单元测试（不连库）。

覆盖不需要数据库就能钉死的事：请求 schema 的上限与白名单、ASIN 规范化/去重/逐条拒绝、
以及**摘要函数的字段边界**——那是 `request_hash` 全部意义所在，且是纯函数，不必连库。

写库才有意义的部分（幂等回放、同键异体 409、跨用户同键、基线只落一次）在
`test_competitor_asin_integration.py`（TEST_DATABASE_URL gate）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.table.competitor import asin_crud as crud
from app.table.competitor.asin_schemas import (
    MAX_TITLE_LEN,
    AsinReportItem,
    AsinReportRequest,
)
from app.table.competitor.asin_service import _normalize_items
from config import settings

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _req(**kw) -> AsinReportRequest:
    base = {
        "request_id": "rq-1",
        "seller_id": "A1TZ946F8HZTNY",
        "site": "US",
        "items": [AsinReportItem(asin="B0TEST1234")],
    }
    base.update(kw)
    return AsinReportRequest(**base)


# ---------- 请求 schema ----------


def test_幂等键必填():
    """可选的幂等键等于没有：省略它的客户端得不到保护，服务端也分不出这是不是新观测。"""
    with pytest.raises(ValidationError):
        AsinReportRequest(seller_id="A1", site="US", items=[])


def test_幂等键不能为空串():
    with pytest.raises(ValidationError):
        _req(request_id="")


def test_条数超限整批拒绝():
    """超限意味着采集端手上比送出的多，写入即沉默截断——而截断后的记录与"这家店只有这些
    品"在数据上完全一致，事后无法分辨。所以在解析阶段就变成 422，一条都不写。"""
    cap = settings.COMPETITOR_MAX_REPORT_ITEMS
    items = [AsinReportItem(asin=f"B0{i:08d}") for i in range(cap + 1)]
    with pytest.raises(ValidationError):
        _req(items=items)


def test_恰好等于上限可以通过():
    cap = settings.COMPETITOR_MAX_REPORT_ITEMS
    items = [AsinReportItem(asin=f"B0{i:08d}") for i in range(cap)]
    assert len(_req(items=items).items) == cap


def test_请求体里的user_id被无视():
    """归属一律从令牌取。用 extra="forbid" 变成 422 反而暗示"这个字段是被认识的"。"""
    req = _req(user_id=999)
    assert not hasattr(req, "user_id")


# ---------- 条目规范化 ----------


def test_asin统一大写去空白():
    rows, rejected = _normalize_items(_req(items=[AsinReportItem(asin="  b0test1234 ")]))
    assert [r["asin"] for r in rows] == ["B0TEST1234"]
    assert rejected == []


def test_同一请求内重复的asin去重而不拒绝():
    """同一个品出现在页面两处是采集端的常态，据此拒绝整批是过度反应。"""
    rows, rejected = _normalize_items(
        _req(items=[AsinReportItem(asin="B0TEST1234"), AsinReportItem(asin="b0test1234")])
    )
    assert len(rows) == 1
    assert rejected == []


def test_格式非法的条目逐条拒绝其余照写():
    """丢弃一条坏数据不制造完整性假象；原因回传能让采集端作者发现选择器取错了元素。"""
    rows, rejected = _normalize_items(
        _req(
            items=[
                AsinReportItem(asin="B0TEST1234"),
                AsinReportItem(asin="不是ASIN"),
                AsinReportItem(asin="B0TEST5678"),
            ]
        )
    )
    assert [r["asin"] for r in rows] == ["B0TEST1234", "B0TEST5678"]
    assert [(r.asin, r.reason) for r in rejected] == [("不是ASIN", "asin_invalid")]


def test_空asin被拒且原因可分辨():
    _, rejected = _normalize_items(_req(items=[AsinReportItem(asin="   ")]))
    assert [r.reason for r in rejected] == ["asin_empty"]


def test_非B0开头的asin不被拒():
    """德国站图书的 ASIN 就是 ISBN-10、不以 B0 开头。按前缀判会把图书类目整个拒掉。"""
    rows, rejected = _normalize_items(_req(items=[AsinReportItem(asin="3548060785")]))
    assert [r["asin"] for r in rows] == ["3548060785"]
    assert rejected == []


def test_超长标题截断而不拒绝():
    """展示用的附属属性，一个过长的标题不该让整批观测数据丢失。"""
    rows, rejected = _normalize_items(
        _req(items=[AsinReportItem(asin="B0TEST1234", title="标" * (MAX_TITLE_LEN + 50))])
    )
    assert len(rows[0]["title"]) == MAX_TITLE_LEN
    assert rejected == []


# ---------- 摘要的字段边界 ----------


def _hash(**kw) -> str:
    base = {
        "seller_id": "A1",
        "site": "US",
        "asins": ["B0TEST1234"],
        "captured_at": _NOW,
        "ship_to_region": "US",
        "page_context": "p1",
    }
    base.update(kw)
    return crud.compute_request_hash(**base)


def test_asin顺序不影响摘要():
    """顺序是采集端遍历 DOM 的产物，同一次观测换个顺序仍是同一次观测。"""
    assert _hash(asins=["B0AAAAAAAA", "B0BBBBBBBB"]) == _hash(asins=["B0BBBBBBBB", "B0AAAAAAAA"])


def test_asin大小写与重复不影响摘要():
    assert _hash(asins=["b0aaaaaaaa", "B0AAAAAAAA"]) == _hash(asins=["B0AAAAAAAA"])


def test_换卖家摘要就变():
    assert _hash(seller_id="A1") != _hash(seller_id="A2")


def test_换站点摘要就变():
    assert _hash(site="US") != _hash(site="GB")


def test_换asin集合摘要就变():
    assert _hash(asins=["B0AAAAAAAA"]) != _hash(asins=["B0AAAAAAAA", "B0BBBBBBBB"])


def test_采集时刻进摘要():
    """这条曾被写反过。重新采集产生新的 captured_at，那是**另一次观测**，本该带新的键；
    把它当成重放静默丢掉，正是 request_hash 要防的事。真正的重试重发原 payload，
    captured_at 逐字节相同、摘要必然一致。"""
    other = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
    assert _hash(captured_at=_NOW) != _hash(captured_at=other)


def test_配送地区进摘要():
    assert _hash(ship_to_region="US") != _hash(ship_to_region="JP")


def test_页面上下文进摘要():
    assert _hash(page_context="p1") != _hash(page_context="p2")


def test_缺省与空串可分辨():
    """用固定前缀标记有无，否则 (None, "") 与 ("", None) 会撞出同一个摘要。"""
    assert _hash(ship_to_region=None) != _hash(ship_to_region="")


def test_标题与图片不进摘要():
    """易变的展示属性——同一个品在不同渲染下标题会被截到不同长度；且它们只在首次落定，
    本就不影响写入结果。把它们算进摘要会让重试撞出假 409。"""
    req_a = _req(items=[AsinReportItem(asin="B0TEST1234", title="长标题 A", image_url="a.jpg")])
    req_b = _req(items=[AsinReportItem(asin="B0TEST1234", title="长标题 B", image_url="b.jpg")])
    rows_a, _ = _normalize_items(req_a)
    rows_b, _ = _normalize_items(req_b)
    assert crud.compute_request_hash(
        seller_id="A1", site="US", asins=[r["asin"] for r in rows_a],
        captured_at=_NOW, ship_to_region=None, page_context=None,
    ) == crud.compute_request_hash(
        seller_id="A1", site="US", asins=[r["asin"] for r in rows_b],
        captured_at=_NOW, ship_to_region=None, page_context=None,
    )


def test_分隔符不可能被字段内容伪造():
    """用普通字符做分隔，("a|b", None) 与 ("a", "b") 会撞出同一个摘要。"""
    assert _hash(ship_to_region="a", page_context="b") != _hash(
        ship_to_region="a\x1fb", page_context=None
    )


# ---------- 基线语句 ----------


def test_落基线的语句带is_null保护():
    """`WHERE baseline_at IS NULL` 保证绝不覆盖已有值；改基线会让首批商品整批涌进上新流，
    是这条路唯一能造成大面积假数据的方式。"""
    compiled = str(crud.set_baseline_stmt("A1", "US"))
    assert "baseline_at IS NULL" in compiled


def test_写在售关系不更新已有行():
    """`ON CONFLICT DO NOTHING`，不是 DO UPDATE——否则老品会因为被重新看到而跳进新发现。"""
    compiled = str(
        crud.insert_asins_stmt([{"seller_id": "A1", "site": "US", "asin": "B0TEST1234"}])
    ).upper()
    assert "DO NOTHING" in compiled
    assert "DO UPDATE" not in compiled
