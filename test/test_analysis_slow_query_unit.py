"""`/api/analysis/search` 慢请求结构化诊断（design.md 决策 D 第二层）的单元测试。

不依赖真实 PostgreSQL：真实数据库查询用一个不做任何事的假 ``AsyncSession`` 替代
（返回空结果即可，本测试只关心计时与诊断输出本身），耗时通过 monkeypatch 私有的
分段函数人为制造，不依赖真正的慢 SQL。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pytest

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


async def _fast_available_dates(_db, _site, _view, db_ms=None):
    return [date(2026, 1, 1)]


async def _fast_listed_total(_db, _site, _view, _on, _req, _has_filters, _user_id, db_ms=None):
    return 0


async def _fast_dropped_total(_db, _site, _view, _on, db_ms=None):
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

    req = _req(keyword="anker太阳能充电宝", asins=["B0EXAMPLE1"], categories=["Electronics", "Pet"], competitor_new=True)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await service.search(_FakeSession(), req, user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    msg = records[0].message
    assert records[0].levelno == logging.WARNING
    assert "site=US" in msg and "view=DAY" in msg
    assert "trend_ms" in msg
    assert "categories_count" in msg and "asins_count" in msg
    # 不泄漏检索词或 ASIN 原文
    assert "anker太阳能充电宝" not in msg
    assert "B0EXAMPLE1" not in msg


async def test_app_side_shaping_time_not_misreported_as_db_time(monkeypatch, caplog):
    """慢在纯 Python 的结果整形上（如 `_fetch_trends` 里的 `shape_trends` 之类，这里
    直接用一个不发任何 `db.execute` 的慢函数模拟），`db_total_ms` 必须保持很小、
    `app_ms` 必须占大头——不能像"按阶段耗时求和"那样把这段时间也算成数据库耗时
    （Codex 复审 PR #98 P2：修复前 `db_total_ms` 恒等于 `total_ms`）。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 30)
    _patch_fast_helpers(monkeypatch)

    async def _slow_pure_python_fetch_trends(*_args, **_kwargs):
        # 不调用 db.execute，纯 sleep 模拟"整形很慢"，db_ms 不应因此增长。
        await asyncio.sleep(0.06)
        return {}, {}, {}

    monkeypatch.setattr(service, "_fetch_trends", _slow_pure_python_fetch_trends)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await service.search(_FakeSession(), _req(), user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    msg = records[0].message
    db_total_ms = int(msg.split("db_total_ms(仅 await db.execute 等待时间,含连接获取)=")[1].split(" ")[0])
    app_ms = int(msg.split("app_ms(其余应用处理:结果整形/序列化等)=")[1].split(" ")[0])
    assert db_total_ms < 30, f"db_total_ms 应该只反映真正的 db.execute 等待，不应吃掉 sleep 的时间，实际 {db_total_ms}"
    assert app_ms >= 50, f"纯 Python 的慢耗时应该归入 app_ms，实际 {app_ms}"


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


async def test_no_data_scope_fast_skips_diagnostic(monkeypatch, caplog):
    """该站点该视图无任何数据、且解析过程本身很快时不产出诊断——空结果不等于
    "值得诊断"，只有真正超阈值才输出。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 5000)

    async def _no_dates(_db, _site, _view, db_ms=None):
        return []

    monkeypatch.setattr(service, "_available_dates", _no_dates)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        resp = await service.search(_FakeSession(), _req(), user_id=1)

    assert resp.total == 0
    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert not records


async def test_timed_execute_records_elapsed_time_even_on_failure():
    """``_timed_execute`` 用 ``finally`` 而不是"执行完再记"——语句超时/锁等待超时/
    连接池 checkout 超时会让 ``await db.execute(...)`` 直接抛出，此前这种情况下
    完全不记录这次等待耗时（Codex 复审 PR #98 P2）。"""
    db_ms: list[int] = []

    class _FailingSession:
        async def execute(self, _stmt):
            await asyncio.sleep(0.05)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await service._timed_execute(_FailingSession(), "stmt", db_ms)

    assert len(db_ms) == 1
    assert db_ms[0] >= 40


async def test_failed_request_still_emits_diagnostic_marked_failed(monkeypatch, caplog):
    """请求中途失败（这里模拟 ``_fetch_trends`` 本身超时/失败）不能让诊断彻底消失
    ——此前 ``search()`` 一旦抛异常就直接跳过结尾的诊断输出，而这恰恰是最该被诊断
    出来的一类失败（连接卡死、锁等待，见 issue #4）（Codex 复审 PR #98 P2）。原始
    异常必须原样重新抛出，不能被诊断逻辑吞掉。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 30)
    _patch_fast_helpers(monkeypatch)

    async def _slow_then_failing_fetch_trends(*_args, **_kwargs):
        await asyncio.sleep(0.06)
        raise RuntimeError("模拟连接卡死或锁等待超时")

    monkeypatch.setattr(service, "_fetch_trends", _slow_then_failing_fetch_trends)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        with pytest.raises(RuntimeError, match="模拟连接卡死或锁等待超时"):
            await service.search(_FakeSession(), _req(), user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    assert "request_failed=True" in records[0].message


async def test_cancelled_request_still_emits_diagnostic_marked_cancelled(monkeypatch, caplog):
    """``asyncio.CancelledError`` 继承自 ``BaseException`` 而不是 ``Exception``
    （3.8+），普通的 ``except Exception`` 捕不到它——Uvicorn 优雅关闭超时/应用层
    超时都是通过取消这个请求的 Task 实现的。不单独捕获的话，
    ``app/common/request_logging.py`` 只会记一条通用的 ``outcome=cancelled``
    摘要，没有这里的 site/view/filters/stage_ms 明细，而取消往往发生在请求已经
    很慢的时候，明细恰恰最有价值（Codex 复审 PR #98 第三轮 P2）。原始的
    ``CancelledError`` 必须原样重新抛出，不能被诊断逻辑吞掉——吞掉会破坏取消语义
    本身。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 30)
    _patch_fast_helpers(monkeypatch)

    async def _slow_then_cancelled_fetch_trends(*_args, **_kwargs):
        await asyncio.sleep(0.06)
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "_fetch_trends", _slow_then_cancelled_fetch_trends)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        with pytest.raises(asyncio.CancelledError):
            await service.search(_FakeSession(), _req(), user_id=1)

    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    assert "request_failed=True" in records[0].message
    assert "request_cancelled=True" in records[0].message


async def test_no_data_scope_but_slow_scope_resolution_still_diagnoses(monkeypatch, caplog):
    """无可用日期的早退路径此前会无条件跳过诊断，哪怕 `_available_dates` 本身因连接池
    排队/锁等待而慢——那恰恰是最该被诊断出来的情形之一，却因为"结果集是空的"被短路
    掉了（Codex 复审 PR #98 P2）。这里让它变慢，确认早退路径现在也会输出诊断。"""
    monkeypatch.setattr(settings, "ANALYSIS_SLOW_QUERY_THRESHOLD_MS", 30)

    async def _slow_no_dates(_db, _site, _view, db_ms=None):
        await asyncio.sleep(0.06)
        return []

    monkeypatch.setattr(service, "_available_dates", _slow_no_dates)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        resp = await service.search(_FakeSession(), _req(), user_id=1)

    assert resp.total == 0
    records = [r for r in caplog.records if "分析查询慢请求诊断" in r.message]
    assert len(records) == 1
    assert "on=None" in records[0].message
