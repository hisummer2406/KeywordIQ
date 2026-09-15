"""`/api/analysis/search` 慢请求结构化诊断（design.md 决策 D 第二层）的单元测试。

不依赖真实 PostgreSQL：真实数据库查询用一个不做任何事的假 ``AsyncSession`` 替代
（返回空结果即可，本测试只关心计时与诊断输出本身），耗时通过 monkeypatch 私有的
分段函数人为制造，不依赖真正的慢 SQL。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.table.analysis import analysis_service as service
from app.table.search.search_schemas import AnalysisSearchRequest
from config import settings

_LOGGER_NAME = "app.table.analysis.analysis_service"


class _FakeResult:
    def all(self):
        return []

    def first(self):
        return None

    def scalar_one_or_none(self):
        return 0

    def scalar_one(self):
        return 0


class _FakeSession:
    """`db.execute(...)` 一律立即返回空结果——本测试的耗时全部来自 monkeypatch
    过的分段函数，不需要假会话本身模拟延迟。"""

    async def execute(self, _stmt):
        return _FakeResult()


async def _fast_available_dates(_db, _site, _view):
    return [date(2026, 1, 1)]


async def _fast_listed_total(_db, _site, _view, _on, _req, _has_filters, _user_id):
    return 0


async def _fast_dropped_total(_db, _site, _view, _on):
    return 0


def _req(**kw) -> AnalysisSearchRequest:
    # include="LISTED" 让分段计划跳过掉榜段，假会话只需要应付 search_stmt 与
    # slice_has_brand_data_stmt 两条查询。
    return AnalysisSearchRequest(site="US", view="DAY", include="LISTED", **kw)


def _patch_fast_helpers(monkeypatch):
    monkeypatch.setattr(service, "_available_dates", _fast_available_dates)
    monkeypatch.setattr(service, "_listed_total", _fast_listed_total)
    monkeypatch.setattr(service, "_dropped_total", _fast_dropped_total)


async def test_slow_request_emits_diagnostic_with_whitelisted_fields(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 30)
    _patch_fast_helpers(monkeypatch)

    async def _slow_fetch_trends(*_args, **_kwargs):
        await asyncio.sleep(0.06)
        return {}, {}, {}

    monkeypatch.setattr(service, "_fetch_trends", _slow_fetch_trends)

    req = _req(keyword="anker太阳能充电宝", asin="B0EXAMPLE1", categories=["Electronics", "Pet"], competitor_new=True)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await service.search(_FakeSession(), req, user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    msg = records[0].message
    assert records[0].levelno == logging.WARNING
    assert "site=US" in msg and "view=DAY" in msg
    assert "trend_ms" in msg
    assert "categories_count" in msg and "has_asin_filter" in msg
    # 不泄漏检索词或 ASIN 原文
    assert "anker太阳能充电宝" not in msg
    assert "B0EXAMPLE1" not in msg


async def test_fast_request_does_not_emit_diagnostic(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 5000)
    _patch_fast_helpers(monkeypatch)

    async def _fast_fetch_trends(*_args, **_kwargs):
        return {}, {}, {}

    monkeypatch.setattr(service, "_fetch_trends", _fast_fetch_trends)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await service.search(_FakeSession(), _req(), user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert not records


async def test_no_data_scope_skips_diagnostic_entirely(monkeypatch, caplog):
    """该站点该视图无任何数据时提前返回空结果——不产出诊断（design.md：耗时必然
    可忽略）。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 0)

    async def _no_dates(_db, _site, _view):
        return []

    monkeypatch.setattr(service, "_available_dates", _no_dates)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        resp = await service.search(_FakeSession(), _req(), user_id=1)

    assert resp.total == 0
    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert not records
