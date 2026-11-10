"""外观专利核查的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（specs/patent-risk-check、patent-risk-review、patent-risk-provider）：
- 创建：Key 未配置零调用、站点/法域/图片内容校验、令牌幂等与 409、他人记录 404；
- 任务：请求路径不调用供应商；P010→P060 全流程落证据；翻页复用临时地址、候选去重；
  在途互斥；结果未知不自动重发；租约过期转 unknown；未配置时认领即失败；
- 详情：只取尚无成功结果的项，部分失败保留已得证据，越权候选 404；
- 意见：市场/证据归属、条件通过必填、检索未完成不能通过、只追加；
- 并发：两个连接同时认领同一个排队任务，只有一个拿到。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.risk import check_service, task_service
from app.table.risk.provider_client import ZhihuiyaClient
from app.table.risk.risk_errors import (
    CandidateNotFoundError,
    CheckNotFoundError,
    InvalidCheckInputError,
    InvalidImageError,
    InvalidReviewError,
    NothingToFetchError,
    ProviderNotConfiguredError,
    TaskInProgressError,
    TokenConflictError,
)
from app.table.risk.risk_model import RiskCandidate, RiskProviderCall, RiskTask
from app.table.risk.risk_schemas import CheckCreate, EvidenceRequest, ReviewCreate
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "ZHIHUIYA_API_KEY", "sk-test-integration")


@pytest.fixture()
async def conn():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    try:
        probe = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
        if await probe.scalar(text("SELECT to_regclass('analysis.risk_check')")) is None:
            pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
        await probe.close()
        yield connection
    finally:
        await trans.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture()
def factory(conn):
    """每次给出绑定在同一外层事务上的新会话：service 的 commit 只是释放保存点。"""
    return lambda: AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


@pytest.fixture()
async def db(factory):
    s = factory()
    yield s
    await s.close()


class Vendor:
    """记录真实客户端发来的每个请求，按路径返回预设响应。"""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, object] = {}
        self.total = 45

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        route = self.routes.get(req.url.path)
        if callable(route):
            return route(req)
        if route is not None:
            return route  # type: ignore[return-value]
        if req.url.path == "/image-search/image-upload":
            return _ok({"url": "https://static/tmp.png", "expire": 32400})
        if req.url.path == "/search/patent/image-single":
            import json
            body = json.loads(req.content)
            off, lim = body["offset"], body["limit"]
            # 第二页与第一页有一条重叠，验证候选去重
            shift = 1 if off else 0
            items = [{"patent_id": f"P{i}", "patent_pn": f"USD{i}", "title": f"<b>t{i}</b>",
                      "url": "https://img/a.png" if i % 2 else "javascript:alert(1)", "score": 1 - i / 100,
                      "authority": "US", "apdt": 20200101, "pbdt": 20210101, "loc": ["07-01"]}
                     for i in range(off - shift, min(off + lim, self.total) - shift)]
            return _ok({"total_search_result_count": self.total, "patent_messages": items})
        ids = req.url.params["patent_id"].split(",")
        if req.url.path.endswith("simple-bibliography"):
            return _ok([{"patent_id": i, "pn": i, "bibliographic_data": {
                "invention_title": [{"lang": "en", "text": "Cup"}],
                "parties": {"assignees": [{"name": "ACME"}]}}} for i in ids])
        return _ok([{"patent_id": i, "legal_date": 20240101,
                     "patent_legal": {"simple_legal_status": ["Active"], "legal_status": ["Granted"]}}
                    for i in ids])

    def client(self) -> ZhihuiyaClient:
        return ZhihuiyaClient("sk-test-integration", timeout=5, transport=httpx.MockTransport(self.handler))


def _ok(data):
    return httpx.Response(200, json={"status": True, "error_code": 0, "data": data})


async def _user(db, name="u"):
    uid = (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,false) RETURNING id"), {"n": f"risk-{name}-{uuid.uuid4().hex[:6]}"})).scalar()
    await db.commit()
    return uid


def _req(**kw) -> CheckCreate:
    base = dict(product_name="水杯", product_version="v1", markets=["US", "GB"], jurisdictions=["US", "GB"],
                page_size=20, client_token=uuid.uuid4().hex)
    base.update(kw)
    return CheckCreate(**base)


async def _run(factory, vendor) -> bool:
    return await task_service.run_one(factory, vendor.client)


async def _searched_check(db, factory, vendor, owner):
    out = await check_service.create_check(db, owner, _req(), PNG)
    assert await _run(factory, vendor)
    return out["id"]


# ---------------------------------------------------------------- 创建与幂等


async def test_create_is_idempotent_and_never_calls_vendor(db, factory):
    owner = await _user(db)
    req = _req()
    first = await check_service.create_check(db, owner, req, PNG)
    again = await check_service.create_check(db, owner, req, PNG)
    assert first == again and first["search_status"] == "queued"
    with pytest.raises(TokenConflictError):
        await check_service.create_check(db, owner, req, JPG)
    n = await db.scalar(select(func.count()).select_from(RiskTask).where(RiskTask.check_id == first["id"]))
    assert n == 1


async def test_not_configured_rejects_without_rows(db, monkeypatch):
    owner = await _user(db)
    monkeypatch.setattr(settings, "ZHIHUIYA_API_KEY", "")
    with pytest.raises(ProviderNotConfiguredError):
        await check_service.create_check(db, owner, _req(), PNG)
    assert check_service.meta()["provider_configured"] is False
    assert "sk-" not in str(check_service.meta())


async def test_input_validation(db):
    owner = await _user(db)
    with pytest.raises(InvalidCheckInputError):
        await check_service.create_check(db, owner, _req(markets=["XX"]), PNG)
    with pytest.raises(InvalidCheckInputError):
        await check_service.create_check(db, owner, _req(jurisdictions=["ZZ"]), PNG)
    with pytest.raises(InvalidImageError):
        await check_service.create_check(db, owner, _req(), GIF)
    with pytest.raises(InvalidImageError):
        await check_service.create_check(db, owner, _req(), PNG + b"\x00" * (4096 * 1024))


async def test_other_users_get_404(db):
    a, b = await _user(db, "a"), await _user(db, "b")
    cid = (await check_service.create_check(db, a, _req(), PNG))["id"]
    for call in (check_service.get_check(db, b, cid), check_service.get_image(db, b, cid),
                 check_service.request_next_page(db, b, cid, uuid.uuid4().hex)):
        with pytest.raises(CheckNotFoundError):
            await call
    with pytest.raises(CheckNotFoundError):
        await check_service.create_check(db, b, _req(previous_check_id=cid), PNG)
    data, ctype = await check_service.get_image(db, a, cid)
    assert data == PNG and ctype == "image/png"


# ---------------------------------------------------------------- 任务执行


async def test_search_flow_saves_evidence(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    cid = await _searched_check(db, factory, vendor, owner)
    assert vendor.paths() == ["/image-search/image-upload", "/search/patent/image-single"]
    assert vendor.requests[0].headers["authorization"] == "Bearer sk-test-integration"

    detail = await check_service.get_check(db, owner, cid)
    assert detail["search_status"] == "completed" and detail["search_completed"]
    assert (detail["fetched_count"], detail["total_count"]) == (20, 45)
    assert len(detail["candidates"]) == 20 and detail["can_fetch_more"]
    # 非 https 附图地址被丢弃
    assert {c["image_url"] for c in detail["candidates"]} == {"https://img/a.png", None}
    assert {(c["api"], c["outcome"]) for c in detail["calls"]} == {("P010", "success"), ("P060", "success")}

    # 刷新 / 轮询 / 再跑一轮都不产生调用
    await check_service.get_check(db, owner, cid)
    assert not await _run(factory, vendor)
    assert len(vendor.requests) == 2


async def test_next_page_reuses_upload_and_dedupes(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    cid = await _searched_check(db, factory, vendor, owner)
    token = uuid.uuid4().hex
    first = await check_service.request_next_page(db, owner, cid, token)
    assert await check_service.request_next_page(db, owner, cid, token) == first  # 双击
    with pytest.raises(TaskInProgressError):
        await check_service.request_next_page(db, owner, cid, uuid.uuid4().hex)
    assert await _run(factory, vendor)
    assert vendor.paths()[-1] == "/search/patent/image-single" and len(vendor.requests) == 3

    detail = await check_service.get_check(db, owner, cid)
    assert detail["fetched_count"] == 40
    # 第二页从 P19 起（来源排序漂移），与第一页重叠一条，去重后 39 条
    assert len(detail["candidates"]) == 39 == len({c["patent_id"] for c in detail["candidates"]})
    assert [p["offset"] for p in detail["pages"]] == [0, 20]


async def test_unknown_result_is_not_retried_and_blocks_pass(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    vendor.routes["/search/patent/image-single"] = httpx.Response(502, text="<html>bad</html>")
    cid = (await check_service.create_check(db, owner, _req(), PNG))["id"]
    assert await _run(factory, vendor)
    detail = await check_service.get_check(db, owner, cid)
    assert detail["search_status"] == "unknown" and detail["can_retry"] and not detail["candidates"]
    assert not await _run(factory, vendor)  # 不自动重发
    calls = len(vendor.requests)

    with pytest.raises(InvalidReviewError):
        await check_service.add_review(db, owner, cid, ReviewCreate(
            client_token=uuid.uuid4().hex, market="US", risk_level="low", decision="pass",
            basis="无命中", uncovered_scope="无"))

    vendor.routes.pop("/search/patent/image-single")
    await check_service.request_retry(db, owner, cid, uuid.uuid4().hex)
    assert await _run(factory, vendor)
    # 临时地址仍有效，重试只调 P060
    assert vendor.paths()[calls:] == ["/search/patent/image-single"]
    assert (await check_service.get_check(db, owner, cid))["search_status"] == "completed"


async def test_vendor_error_fails_with_category(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    vendor.routes["/image-search/image-upload"] = httpx.Response(
        200, json={"status": False, "error_code": 67200005, "error_msg": "余额不足"})
    cid = (await check_service.create_check(db, owner, _req(), PNG))["id"]
    assert await _run(factory, vendor)
    detail = await check_service.get_check(db, owner, cid)
    assert detail["search_status"] == "failed"
    assert detail["tasks"][0]["error_category"] == "insufficient_balance"
    assert vendor.paths() == ["/image-search/image-upload"]  # 上传失败不再检索


async def test_not_configured_at_claim_fails_without_calls(db, factory, monkeypatch):
    owner = await _user(db)
    cid = (await check_service.create_check(db, owner, _req(), PNG))["id"]
    monkeypatch.setattr(settings, "ZHIHUIYA_API_KEY", "")
    vendor = Vendor()
    assert await _run(factory, vendor)
    assert vendor.requests == []
    detail = await check_service.get_check(db, owner, cid)
    assert detail["search_status"] == "failed" and detail["tasks"][0]["error_category"] == "not_configured"


async def test_expired_lease_becomes_unknown(db, factory):
    owner = await _user(db)
    cid = (await check_service.create_check(db, owner, _req(), PNG))["id"]
    async with factory() as s:
        snap = await task_service._claim(s)
    assert snap.check_id == cid
    await db.execute(update(RiskTask).where(RiskTask.id == snap.task_id)
                     .values(lease_until=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()
    assert await task_service.reap_expired(factory) == 1
    detail = await check_service.get_check(db, owner, cid)
    assert detail["search_status"] == "unknown" and detail["tasks"][0]["error_category"] == "interrupted"
    vendor = Vendor()
    assert not await _run(factory, vendor) and vendor.requests == []


# ---------------------------------------------------------------- 详情


async def test_details_partial_failure_and_no_refetch(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    cid = await _searched_check(db, factory, vendor, owner)
    ids = [c["id"] for c in (await check_service.get_check(db, owner, cid))["candidates"][:2]]

    def timeout(req):
        raise httpx.ReadTimeout("t", request=req)

    vendor.routes["/basic-patent-data/legal-status"] = timeout
    await check_service.request_details(db, owner, cid, EvidenceRequest(client_token=uuid.uuid4().hex,
                                                                          candidate_ids=ids))
    assert await _run(factory, vendor)
    cands = {c["id"]: c for c in (await check_service.get_check(db, owner, cid))["candidates"]}
    for i in ids:
        assert cands[i]["details"]["bibliography"]["status"] == "success"
        assert cands[i]["details"]["bibliography"]["data"]["assignees"] == ["ACME"]
        assert cands[i]["details"]["legal"]["status"] == "unknown"

    vendor.routes.pop("/basic-patent-data/legal-status")
    before = len(vendor.requests)
    await check_service.request_details(db, owner, cid, EvidenceRequest(client_token=uuid.uuid4().hex,
                                                                          candidate_ids=ids))
    assert await _run(factory, vendor)
    assert vendor.paths()[before:] == ["/basic-patent-data/legal-status"]  # 著录已成功，不再取
    cands = {c["id"]: c for c in (await check_service.get_check(db, owner, cid))["candidates"]}
    assert cands[ids[0]]["details"]["legal"]["data"]["simple_legal_status"] == ["Active"]

    skipped = await check_service.request_details(db, owner, cid, EvidenceRequest(
        client_token=uuid.uuid4().hex, candidate_ids=ids))
    assert skipped["skipped"] is True and len(vendor.requests) == before + 1


async def test_details_reject_foreign_candidate(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    a = await _searched_check(db, factory, vendor, owner)
    b = await _searched_check(db, factory, vendor, owner)
    foreign = await db.scalar(select(RiskCandidate.id).where(RiskCandidate.check_id == b).limit(1))
    with pytest.raises(CandidateNotFoundError):
        await check_service.request_details(db, owner, a, EvidenceRequest(
            client_token=uuid.uuid4().hex, candidate_ids=[foreign]))


async def test_no_more_pages(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    vendor.total = 5
    cid = await _searched_check(db, factory, vendor, owner)
    with pytest.raises(NothingToFetchError):
        await check_service.request_next_page(db, owner, cid, uuid.uuid4().hex)


# ---------------------------------------------------------------- 人工意见


async def test_review_rules_and_append_only(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    cid = await _searched_check(db, factory, vendor, owner)
    cand = (await check_service.get_check(db, owner, cid))["candidates"][0]["id"]

    def rv(**kw):
        base = dict(client_token=uuid.uuid4().hex, market="GB", risk_level="high", decision="not_recommended",
                    basis="外形近似", uncovered_scope="未检索 WO")
        base.update(kw)
        return ReviewCreate(**base)

    with pytest.raises(InvalidReviewError):
        await check_service.add_review(db, owner, cid, rv(market="DE"))
    with pytest.raises(InvalidReviewError):
        await check_service.add_review(db, owner, cid, rv(evidence_candidate_ids=[999999999]))
    with pytest.raises(InvalidReviewError):
        await check_service.add_review(db, owner, cid, rv(decision="conditional_pass", conditions="改外形",
                                                           evidence_candidate_ids=[cand]))

    first = rv()
    await check_service.add_review(db, owner, cid, first)
    assert (await check_service.add_review(db, owner, cid, first))["decision"] == "not_recommended"
    with pytest.raises(TokenConflictError):
        await check_service.add_review(db, owner, cid, first.model_copy(update={"basis": "改了"}))
    await check_service.add_review(db, owner, cid, rv(decision="conditional_pass", risk_level="medium",
                                                       conditions="改外形", evidence_candidate_ids=[cand],
                                                       review_date=date(2027, 1, 1)))
    reviews = (await check_service.get_check(db, owner, cid))["reviews"]
    assert [r["decision"] for r in reviews["GB"]] == ["conditional_pass", "not_recommended"]
    assert reviews["US"] == []  # US 未审核，不会显示为通过

    listed = await check_service.list_checks(db, owner, page=1, page_size=10, review_state="partial")
    assert [i["id"] for i in listed["items"]] == [cid] and listed["items"][0]["reviewed_markets"] == 1
    assert (await check_service.list_checks(db, owner, page=1, page_size=10, review_state="complete"))["total"] == 0


async def test_new_round_links_previous(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    a = await _searched_check(db, factory, vendor, owner)
    b = (await check_service.create_check(db, owner, _req(product_version="v2", previous_check_id=a), JPG))["id"]
    assert (await check_service.get_check(db, owner, b))["previous_check_id"] == a
    assert (await check_service.get_check(db, owner, a))["next_check_ids"] == [b]
    assert (await check_service.get_check(db, owner, b))["reviews"] == {"US": [], "GB": []}


# ---------------------------------------------------------------- 并发认领（真提交）


async def test_concurrent_claim_only_one_wins():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        if await s.scalar(text("SELECT to_regclass('analysis.risk_check')")) is None:
            pytest.skip("测试库未迁移")
        owner = await _user(s, "race")
        cid = (await check_service.create_check(s, owner, _req(), PNG))["id"]
    try:
        # 其他排队任务（共享库残留）不能干扰：只看本核查单的任务被几个连接拿到
        async def claim():
            async with AsyncSession(engine, expire_on_commit=False) as s:
                return await task_service._claim(s)

        results = await asyncio.gather(claim(), claim())
        mine = [r for r in results if isinstance(r, task_service.Snapshot) and r.check_id == cid]
        assert len(mine) == 1
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(text("DELETE FROM analysis.risk_check WHERE owner_id = :o"), {"o": owner})
            await s.execute(text("DELETE FROM analysis.user_center WHERE id = :o"), {"o": owner})
            await s.commit()
        await engine.dispose()


async def test_calls_recorded_with_unknown_billing(db, factory):
    owner = await _user(db)
    vendor = Vendor()
    cid = await _searched_check(db, factory, vendor, owner)
    billing = (await db.execute(
        select(RiskProviderCall.billing).join(RiskTask, RiskTask.id == RiskProviderCall.task_id)
        .where(RiskTask.check_id == cid))).scalars().all()
    assert billing == ["unknown", "unknown"]
