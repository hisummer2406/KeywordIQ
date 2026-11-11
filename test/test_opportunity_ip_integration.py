"""机会中心 IP 专家的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（specs/opportunity-ip-agent）：
- 私有：他人调研 404；
- 请求路径不调用外部服务；检索式只有人工提交才执行；
- 预算：单次调研与全站每日上限，失败与未知的调用也计入；
- 幂等与在途互斥；
- 全流程（假 MCP、假大模型）：计划 → 检索 → 取证（含附图下载）→ 分析；
- 无命中、失败、未知分开；取证只取尚无成功结果的项；租约过期转 unknown；
- 大模型未配置时人工路径仍可用；分析输出校验；
- 人工意见沿用 #24 规则，Agent 不写意见。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.opportunity import research_service as svc
from app.table.opportunity import task_service
from app.table.opportunity.ip_errors import (
    BudgetExceededError,
    CandidateNotFoundError,
    LlmNotConfiguredError,
    McpNotConfiguredError,
    NothingToAnalyzeError,
    NothingToFetchError,
    ResearchNotFoundError,
)
from app.table.opportunity.ip_model import OppIpCall, OppIpReview, OppIpSchedule, OppIpTask
from app.table.opportunity.ip_schemas import DetailRequest, QueryItem, ResearchCreate, ReviewCreate, SearchRequest
from app.table.opportunity.llm_client import LlmClient
from app.table.opportunity.mcp_client import McpSession
from app.table.risk.risk_errors import InvalidReviewError, TaskInProgressError, TokenConflictError
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "ZHIHUIYA_API_KEY", "sk-test-integration")
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "sk-llm-test")
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_MODEL", "test-model")
    monkeypatch.setattr(settings, "OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH", 20)
    monkeypatch.setattr(settings, "OPPORTUNITY_IP_MCP_DAILY_LIMIT", 10_000)


@pytest.fixture()
async def conn():
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    try:
        probe = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
        if await probe.scalar(text("SELECT to_regclass('analysis.opp_ip_research')")) is None:
            pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
        await probe.close()
        yield connection
    finally:
        await trans.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture()
def factory(conn):
    return lambda: AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


@pytest.fixture()
async def db(factory):
    s = factory()
    yield s
    await s.close()


class Vendor:
    """假 MCP（按工具名路由）+ 假大模型 + 假附图源。记录每次工具调用。"""

    def __init__(self):
        self.tool_calls: list[tuple[str, dict]] = []
        self.llm_calls: list[dict] = []
        self.search: dict[str, object] = {}  # query_text → 响应（dict 为 structuredContent，或 httpx.Response）
        self.llm_reply: object = {"queries": [{"query_text": "TTL:(cup) AND LOC:(07-01)", "rationale": "核心"}]}
        self.image_status = 200

    def _sse(self, payload):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=f"data:{json.dumps(payload)}\n\n")

    def mcp_handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body.get("method") != "tools/call":
            return self._sse({"jsonrpc": "2.0", "id": body.get("id"), "result": {}})
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.tool_calls.append((name, args))
        if name == "search_patents":
            r = self.search.get(args["query_text"])
            if isinstance(r, httpx.Response):
                return r
            sc = r if r is not None else {"success": True, "error_code": 0, "data": {
                "result_count": 3, "total_search_result_count": 120,
                "results": [{"patent_id": f"P{i}", "pn": f"USD{i}", "title": f"Cup {i}", "authority": "US",
                             "apdt": 20200101, "current_assignee": "ACME"} for i in range(3)]}}
        elif name == "get_patent_legal_status":
            sc = {"success": True, "error_code": 0,
                  "data": [{"patent_id": args["patent_id"], "pn": "USD", "simple_legal_status": ["有效"]}]}
        elif name == "bibliography":
            sc = {"success": True, "error_code": 0, "data": [{"patent_id": args["patent_id"], "pn": "USD",
                  "bibliographic_data": {"patent_type": "DESIGN",
                                         "classification_data": {"classification_loc": ["07-01"]}}}]}
        else:
            sc = {"success": True, "error_code": 0, "data": [{"patent_id": args["patent_id"], "pn": "USD",
                  "abstract_drawing": {"num": "0001", "path": "https://data-abstract-image.zhihuiya.com/x.png?sign=s"}}]}
        return self._sse({"jsonrpc": "2.0", "id": body["id"], "result": {"structuredContent": sc, "isError": False}})

    def llm_handler(self, req: httpx.Request) -> httpx.Response:
        self.llm_calls.append(json.loads(req.content))
        reply = self.llm_reply(self) if callable(self.llm_reply) else self.llm_reply
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    def image_handler(self, req: httpx.Request) -> httpx.Response:
        return httpx.Response(self.image_status, content=PNG)

    def mcp(self) -> McpSession:
        return McpSession("sk-test-integration", timeout=5, transport=httpx.MockTransport(self.mcp_handler))

    def llm(self) -> LlmClient:
        return LlmClient(base_url="https://llm.example.com/v1", api_key="k", model="test-model", timeout=5,
                         transport=httpx.MockTransport(self.llm_handler))


@pytest.fixture()
def vendor(monkeypatch):
    v = Vendor()
    monkeypatch.setattr(task_service, "image_transport", httpx.MockTransport(v.image_handler))
    return v


async def _run(factory, vendor) -> bool:
    return await task_service.run_one(factory, vendor.mcp, vendor.llm)


async def _user(db, name="u"):
    uid = (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,true) RETURNING id"), {"n": f"oppip-{name}-{uuid.uuid4().hex[:6]}"})).scalar()
    await db.commit()
    return uid


def _tok() -> str:
    return uuid.uuid4().hex


def _create(**kw) -> ResearchCreate:
    base = dict(client_token=_tok(), product_name="陶瓷马克杯", description="带把手", keywords=["mug", "cup"],
                loc_classes=["07-01"], jurisdictions=["US", "EU"], markets=["US", "DE"])
    base.update(kw)
    return ResearchCreate(**base)


def _search(*queries: str, limit: int = 10) -> SearchRequest:
    return SearchRequest(client_token=_tok(), queries=[QueryItem(query_text=q, limit=limit) for q in queries])


async def _research(db, owner, **kw) -> int:
    return (await svc.create_research(db, owner, _create(**kw)))["id"]


async def _mcp_calls(db, research_id=None) -> int:
    conds = [OppIpCall.provider == "mcp"]
    if research_id:
        conds.append(OppIpCall.research_id == research_id)
    return (await db.execute(select(func.count()).select_from(OppIpCall).where(*conds))).scalar_one()


# ---------------------------------------------------------------- 创建、私有、幂等


async def test_create_idempotent_and_private(db):
    owner, other = await _user(db), await _user(db, "o")
    req = _create()
    first = await svc.create_research(db, owner, req)
    assert await svc.create_research(db, owner, req) == first
    with pytest.raises(TokenConflictError):
        await svc.create_research(db, owner, req.model_copy(update={"product_name": "别的"}))
    with pytest.raises(ResearchNotFoundError):
        await svc.get_research(db, other, first["id"])
    listing = await svc.list_researches(db, other, page=1, page_size=20)
    assert all(i["id"] != first["id"] for i in listing["items"])


async def test_unknown_market_or_jurisdiction_rejected(db):
    owner = await _user(db)
    from app.table.opportunity.ip_errors import InvalidResearchInputError
    with pytest.raises(InvalidResearchInputError):
        await svc.create_research(db, owner, _create(markets=["XX"]))
    with pytest.raises(InvalidResearchInputError):
        await svc.create_research(db, owner, _create(jurisdictions=["ZZ"]))


# ---------------------------------------------------------------- 全流程


async def test_full_flow_plan_search_detail_analysis(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)

    # 计划：只调大模型，不调 MCP；方案不会自动执行
    await svc.request_plan(db, owner, rid, _tok())
    assert vendor.tool_calls == []
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert d["plans"][0]["content"]["queries"][0]["query_text"] == "TTL:(cup) AND LOC:(07-01)"
    assert vendor.tool_calls == [] and d["queries"] == [] and d["budget"]["used"] == 0
    assert "陶瓷马克杯" in vendor.llm_calls[0]["messages"][1]["content"]

    # 人工确认两条检索式：候选按 patent_id 去重
    await svc.request_search(db, owner, rid, _search("TTL:(cup) AND LOC:(07-01)", "TA:(mug)"))
    assert vendor.tool_calls == []  # 请求路径不调用
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert [q["status"] for q in d["queries"]] == ["success", "success"]
    assert [q["total_count"] for q in d["queries"]] == [120, 120]
    assert len(d["candidates"]) == 3 and d["search_done"] is True
    assert d["budget"]["used"] == 2

    # 取证：两件 × 三个工具，附图下载存库
    ids = [c["id"] for c in d["candidates"][:2]]
    await svc.request_details(db, owner, rid, DetailRequest(client_token=_tok(), candidate_ids=ids))
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    c0 = next(c for c in d["candidates"] if c["id"] == ids[0])
    assert c0["details"]["get_patent_legal_status"]["data"]["simple_legal_status"] == ["有效"]
    assert c0["details"]["bibliography"]["data"]["loc"] == ["07-01"]
    assert c0["details"]["abstract_image"]["has_image"] is True
    img, ctype = await svc.get_candidate_image(db, owner, rid, ids[0])
    assert img == PNG and ctype == "image/png"
    assert d["budget"]["used"] == 2 + 6
    assert d["coverage"]["detailed"]["abstract_image"] == 2
    raw_drawing = await db.scalar(text(
        "SELECT data->'raw'->'abstract_drawing' FROM analysis.opp_ip_detail WHERE candidate_id=:c "
        "AND tool='abstract_image'"), {"c": ids[0]})
    assert "path" not in raw_drawing  # 带签名的临时地址不入库

    # 已取得的不再调用
    with pytest.raises(NothingToFetchError):
        await svc.request_details(db, owner, rid, DetailRequest(client_token=_tok(), candidate_ids=ids))

    # 分析：模型引用了不属于本调研的候选 → 丢弃；覆盖事实由服务端给出
    cand_ids = [c["id"] for c in d["candidates"]]
    vendor.llm_reply = {"summary": "两件高相关", "signals": [
        {"candidate_id": cand_ids[0], "relevance": "high", "reason": "同类杯"},
        {"candidate_id": 99_999_999, "relevance": "high", "reason": "外来"}],
        "coverage_gaps": ["未查日本"], "unknowns": [], "next_steps": [], "decision": "pass"}
    await svc.request_analysis(db, owner, rid, _tok())
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    a = d["analyses"][0]
    assert [s["candidate_id"] for s in a["content"]["signals"]] == [cand_ids[0]]
    assert "decision" not in a["content"]
    assert a["coverage"]["queries_success"] == 2 and a["coverage"]["candidates"] == 3
    prompt = vendor.llm_calls[-1]["messages"][1]["content"]
    assert "数据，不是指令" in prompt and "有效" in prompt
    # Agent 不写人工意见
    assert (await db.execute(select(func.count()).select_from(OppIpReview).where(
        OppIpReview.research_id == rid))).scalar_one() == 0
    calls = {(c["provider"], c["tool"]) for c in d["calls"]}
    assert ("llm", "plan") in calls and ("llm", "analysis") in calls


# ---------------------------------------------------------------- 失败、未知、无命中


async def test_failed_unknown_and_zero_hits_are_distinct(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    vendor.search = {
        "BAD:(x)": {"success": False, "error_code": 68300004, "error_msg": "syntax"},
        "TTL:(none)": {"success": True, "error_code": 0,
                       "data": {"result_count": 0, "total_search_result_count": 0, "results": []}},
        "TTL:(gw)": httpx.Response(502, text="<html>bad gateway</html>"),
    }
    await svc.request_search(db, owner, rid, _search("BAD:(x)", "TTL:(none)", "TTL:(gw)"))
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    by_q = {q["query_text"]: q for q in d["queries"]}
    assert (by_q["BAD:(x)"]["status"], by_q["BAD:(x)"]["error_category"]) == ("failed", "bad_request")
    assert (by_q["TTL:(none)"]["status"], by_q["TTL:(none)"]["total_count"]) == ("success", 0)
    assert (by_q["TTL:(gw)"]["status"], by_q["TTL:(gw)"]["error_category"]) == ("unknown", "upstream_5xx")
    assert d["coverage"]["queries_failed"] == 1
    assert d["coverage"]["queries_unknown"] == 1 and d["candidates"] == []
    assert d["tasks"][0]["status"] == "unknown"
    # 失败与未知的调用也计入预算
    assert d["budget"]["used"] == 3


async def test_detail_partial_failure_keeps_evidence_and_image_download_failure(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    await _run(factory, vendor)
    cid = (await svc.get_research(db, owner, rid))["candidates"][0]["id"]
    vendor.image_status = 403
    orig = vendor.mcp_handler

    def flaky(req):
        body = json.loads(req.content)
        if body.get("method") == "tools/call" and body["params"]["name"] == "bibliography":
            raise httpx.ReadTimeout("slow", request=req)
        return orig(req)

    vendor.mcp_handler = flaky
    await svc.request_details(db, owner, rid, DetailRequest(client_token=_tok(), candidate_ids=[cid]))
    await _run(factory, vendor)
    det = next(c for c in (await svc.get_research(db, owner, rid))["candidates"] if c["id"] == cid)["details"]
    assert det["get_patent_legal_status"]["status"] == "success"
    assert (det["bibliography"]["status"], det["bibliography"]["error_category"]) == ("unknown", "timeout")
    assert det["abstract_image"]["status"] == "success" and det["abstract_image"]["has_image"] is False

    # 再次取证只取尚无成功结果的项（bibliography）
    vendor.mcp_handler = orig
    before = len(vendor.tool_calls)
    await svc.request_details(db, owner, rid, DetailRequest(client_token=_tok(), candidate_ids=[cid]))
    await _run(factory, vendor)
    assert [t for t, _ in vendor.tool_calls[before:]] == ["bibliography"]


async def test_invalid_llm_output_fails_without_saving(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    vendor.llm_reply = {"queries": [{"query_text": ""}]}
    await svc.request_plan(db, owner, rid, _tok())
    await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert d["plans"] == []
    assert (d["tasks"][0]["status"], d["tasks"][0]["error_category"]) == ("failed", "invalid_output")


# ---------------------------------------------------------------- 预算、互斥、配置


async def test_research_budget_rejects_before_enqueue(db, factory, vendor, monkeypatch):
    monkeypatch.setattr(settings, "OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH", 4)
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(a)", "TTL:(b)"))
    await _run(factory, vendor)
    with pytest.raises(BudgetExceededError) as exc:
        await svc.request_search(db, owner, rid, _search("TTL:(c)", "TTL:(d)", "TTL:(e)"))
    assert "上限 4，已用 2，本次需要 3" in exc.value.message
    assert (await db.execute(select(func.count()).select_from(OppIpTask).where(
        OppIpTask.research_id == rid))).scalar_one() == 1


async def test_daily_budget_counts_in_flight_plans(db, monkeypatch):
    owner = await _user(db)
    used_today = await svc._daily_used(db)
    monkeypatch.setattr(settings, "OPPORTUNITY_IP_MCP_DAILY_LIMIT", used_today + 3)
    a, b = await _research(db, owner), await _research(db, owner)
    await svc.request_search(db, owner, a, _search("TTL:(a)", "TTL:(b)"))  # 在途计划 2
    with pytest.raises(BudgetExceededError) as exc:
        await svc.request_search(db, owner, b, _search("TTL:(c)", "TTL:(d)"))
    assert "今日全站" in exc.value.message


async def test_one_active_task_and_token_idempotent(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    req = _search("TTL:(cup)")
    first = await svc.request_search(db, owner, rid, req)
    assert await svc.request_search(db, owner, rid, req) == first
    with pytest.raises(TokenConflictError):
        await svc.request_search(db, owner, rid, req.model_copy(update={"queries": [QueryItem(query_text="TTL:(x)")]}))
    with pytest.raises(TaskInProgressError):
        await svc.request_plan(db, owner, rid, _tok())


async def test_llm_unconfigured_blocks_agent_but_manual_path_works(db, factory, vendor, monkeypatch):
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "")
    owner = await _user(db)
    rid = await _research(db, owner)
    with pytest.raises(LlmNotConfiguredError):
        await svc.request_plan(db, owner, rid, _tok())
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert d["search_done"] and d["llm_configured"] is False
    with pytest.raises(LlmNotConfiguredError):
        await svc.request_analysis(db, owner, rid, _tok())
    assert vendor.llm_calls == []


async def test_mcp_unconfigured_rejects_and_queued_task_fails_on_claim(db, factory, vendor, monkeypatch):
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    monkeypatch.setattr(settings, "ZHIHUIYA_API_KEY", "")
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert (d["tasks"][0]["status"], d["tasks"][0]["error_category"]) == ("failed", "not_configured")
    assert vendor.tool_calls == []
    with pytest.raises(McpNotConfiguredError):
        await svc.request_search(db, owner, rid, _search("TTL:(x)"))


async def test_analysis_requires_successful_search(db):
    owner = await _user(db)
    rid = await _research(db, owner)
    with pytest.raises(NothingToAnalyzeError):
        await svc.request_analysis(db, owner, rid, _tok())


async def test_foreign_candidate_rejected(db, factory, vendor):
    owner, other = await _user(db), await _user(db, "o")
    mine, theirs = await _research(db, owner), await _research(db, other)
    await svc.request_search(db, other, theirs, _search("TTL:(cup)"))
    await _run(factory, vendor)
    foreign = (await svc.get_research(db, other, theirs))["candidates"][0]["id"]
    with pytest.raises(CandidateNotFoundError):
        await svc.request_details(db, owner, mine, DetailRequest(client_token=_tok(), candidate_ids=[foreign]))


async def test_expired_lease_becomes_unknown_and_is_not_resent(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    await db.execute(update(OppIpTask).where(OppIpTask.research_id == rid).values(
        status="running", lease_until=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()
    assert await task_service.reap_expired(factory) >= 1
    d = await svc.get_research(db, owner, rid)
    assert (d["tasks"][0]["status"], d["tasks"][0]["error_category"]) == ("unknown", "interrupted")
    assert not await _run(factory, vendor)  # 不会被重新认领
    assert vendor.tool_calls == []


# ---------------------------------------------------------------- 人工意见


def _review(**kw) -> ReviewCreate:
    base = dict(client_token=_tok(), market="US", risk_level="medium", decision="need_evidence",
                basis="候选 1 外观接近", uncovered_scope="未查 JP")
    base.update(kw)
    return ReviewCreate(**base)


async def test_review_rules_shared_with_risk(db, factory, vendor):
    owner = await _user(db)
    rid = await _research(db, owner)
    with pytest.raises(InvalidReviewError, match="检索尚未完成"):
        await svc.add_review(db, owner, rid, _review(decision="pass"))
    with pytest.raises(InvalidReviewError, match="市场不在"):
        await svc.add_review(db, owner, rid, _review(market="JP"))

    vendor.search = {"BAD:(x)": {"success": False, "error_code": 68300004}}
    await svc.request_search(db, owner, rid, _search("BAD:(x)"))
    await _run(factory, vendor)
    with pytest.raises(InvalidReviewError, match="检索尚未完成"):  # 只有失败的检索
        await svc.add_review(db, owner, rid, _review(decision="pass"))

    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    await _run(factory, vendor)
    cid = (await svc.get_research(db, owner, rid))["candidates"][0]["id"]
    with pytest.raises(InvalidReviewError, match="条件通过"):
        await svc.add_review(db, owner, rid, _review(decision="conditional_pass"))
    req = _review(decision="conditional_pass", conditions="改把手", evidence_candidate_ids=[cid],
                  review_date=date(2026, 12, 1))
    out = await svc.add_review(db, owner, rid, req)
    assert await svc.add_review(db, owner, rid, req) == out
    await svc.add_review(db, owner, rid, _review())
    d = await svc.get_research(db, owner, rid)
    assert [r["decision"] for r in d["reviews"]["US"]] == ["need_evidence", "conditional_pass"]
    assert d["reviews"]["DE"] == []


# ---------------------------------------------------------------- 工作空间（决策 11）


async def test_workspace_crud_move_and_private(db):
    from app.table.opportunity import workspace_service as ws_svc
    from app.table.opportunity.ip_errors import (
        WorkspaceNameTakenError,
        WorkspaceNotEmptyError,
        WorkspaceNotFoundError,
    )
    owner, other = await _user(db), await _user(db, "o")
    ws = await ws_svc.create_workspace(db, owner, "杯子类")
    with pytest.raises(WorkspaceNameTakenError):
        await ws_svc.create_workspace(db, owner, "杯子类")
    foreign = await ws_svc.create_workspace(db, other, "杯子类")  # 别人可以同名

    rid = await _research(db, owner, workspace_id=ws["id"])
    loose = await _research(db, owner)
    with pytest.raises(WorkspaceNotFoundError):
        await _research(db, owner, workspace_id=foreign["id"])

    listing = await ws_svc.list_workspaces(db, owner)
    assert [(i["name"], i["task_count"]) for i in listing["items"]] == [("杯子类", 1)]
    assert listing["unassigned_count"] == 1 and listing["total_count"] == 2
    in_ws = await svc.list_researches(db, owner, page=1, page_size=20, workspace_id=ws["id"])
    assert [i["id"] for i in in_ws["items"]] == [rid]
    unassigned = await svc.list_researches(db, owner, page=1, page_size=20, unassigned=True)
    assert [i["id"] for i in unassigned["items"]] == [loose]

    with pytest.raises(WorkspaceNotEmptyError):
        await ws_svc.delete_workspace(db, owner, ws["id"])
    with pytest.raises(WorkspaceNotFoundError):
        await svc.move_research(db, owner, rid, foreign["id"])
    with pytest.raises(WorkspaceNotFoundError):
        await ws_svc.rename_workspace(db, other, ws["id"], "改名")

    # 移动不改调研输入：同令牌重放仍返回原记录
    assert (await svc.move_research(db, owner, rid, None))["workspace_id"] is None
    await ws_svc.delete_workspace(db, owner, ws["id"])
    assert (await ws_svc.list_workspaces(db, owner))["items"] == []


# ---------------------------------------------------------------- 对话追问（决策 12）


async def test_ask_saves_messages_cites_only_own_candidates_and_never_calls_mcp(db, factory, vendor):
    from app.table.opportunity.ip_schemas import MessageCreate
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    own = d["candidates"][0]["id"]
    mcp_before = await _mcp_calls(db, rid)

    vendor.llm_reply = {"answer": "USD0 标题相近，建议取证。", "cited_candidate_ids": [own, 999999]}
    await svc.request_ask(db, owner, rid, MessageCreate(client_token=_tok(), content="哪件最像？"))
    # 有在途任务时追问被拒，消息不落库
    with pytest.raises(TaskInProgressError):
        await svc.request_ask(db, owner, rid, MessageCreate(client_token=_tok(), content="第二个问题"))
    assert await _run(factory, vendor)

    d = await svc.get_research(db, owner, rid)
    assert [(m["role"], m["content"]) for m in d["messages"]] == [
        ("user", "哪件最像？"), ("agent", "USD0 标题相近，建议取证。")]
    assert d["messages"][1]["cited_candidate_ids"] == [own]
    assert await _mcp_calls(db, rid) == mcp_before and d["budget"]["used"] == mcp_before
    prompt = vendor.llm_calls[-1]["messages"][1]["content"]
    assert "哪件最像？" in prompt and "USD0" in prompt

    # 第二问带上历史
    vendor.llm_reply = {"answer": "见上。", "cited_candidate_ids": []}
    await svc.request_ask(db, owner, rid, MessageCreate(client_token=_tok(), content="再说一遍"))
    assert await _run(factory, vendor)
    assert "USD0 标题相近" in vendor.llm_calls[-1]["messages"][1]["content"]


async def test_ask_failure_keeps_question_without_answer(db, factory, vendor):
    from app.table.opportunity.ip_schemas import MessageCreate
    owner = await _user(db)
    rid = await _research(db, owner)
    vendor.llm_reply = {"answer": ""}  # 校验不通过
    await svc.request_ask(db, owner, rid, MessageCreate(client_token=_tok(), content="有风险吗"))
    assert await _run(factory, vendor)
    d = await svc.get_research(db, owner, rid)
    assert [m["role"] for m in d["messages"]] == ["user"]
    assert d["tasks"][0]["kind"] == "ask" and d["tasks"][0]["status"] == "failed"
    assert d["tasks"][0]["error_category"] == "invalid_output"


async def test_ask_requires_llm(db, monkeypatch):
    from app.table.opportunity.ip_schemas import MessageCreate
    owner = await _user(db)
    rid = await _research(db, owner)
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "")
    with pytest.raises(LlmNotConfiguredError):
        await svc.request_ask(db, owner, rid, MessageCreate(client_token=_tok(), content="?"))
    assert (await svc.get_research(db, owner, rid))["messages"] == []


# ---------------------------------------------------------------- 定时复检（决策 13）


def test_next_run_weekly_and_monthly():
    from zoneinfo import ZoneInfo

    from app.table.opportunity.schedule_service import next_run
    sh = ZoneInfo("Asia/Shanghai")
    wed_10 = datetime(2026, 11, 11, 10, tzinfo=sh)  # 周三
    assert next_run(wed_10, frequency="weekly", weekday=2, day_of_month=None, hour=9) == datetime(2026, 11, 18, 9, tzinfo=sh)
    assert next_run(wed_10, frequency="weekly", weekday=2, day_of_month=None, hour=11) == datetime(2026, 11, 11, 11, tzinfo=sh)
    assert next_run(wed_10, frequency="weekly", weekday=0, day_of_month=None, hour=9) == datetime(2026, 11, 16, 9, tzinfo=sh)
    dec = datetime(2026, 12, 20, 0, tzinfo=sh)
    assert next_run(dec, frequency="monthly", weekday=None, day_of_month=15, hour=8) == datetime(2027, 1, 15, 8, tzinfo=sh)
    assert next_run(dec, frequency="monthly", weekday=None, day_of_month=28, hour=8) == datetime(2026, 12, 28, 8, tzinfo=sh)


def _sched(*queries: str, **kw):
    from app.table.opportunity.ip_schemas import ScheduleUpsert
    base = dict(frequency="weekly", weekday=0, hour=9, queries=[{"query_text": q, "limit": 10} for q in queries])
    base.update(kw)
    return ScheduleUpsert(**base)


async def test_schedule_only_uses_executed_queries(db, factory, vendor):
    from app.table.opportunity import schedule_service as sch
    from app.table.opportunity.ip_errors import ScheduleNotFoundError, ScheduleQueryNotExecutedError
    owner, other = await _user(db), await _user(db, "o")
    rid = await _research(db, owner)
    with pytest.raises(ScheduleQueryNotExecutedError):
        await sch.upsert_schedule(db, owner, rid, _sched("TTL:(cup)"))
    vendor.search["TTL:(bad"] = {"success": False, "error_code": 68300004, "error_msg": "syntax"}
    await svc.request_search(db, owner, rid, _search("TTL:(cup)", "TTL:(bad"))
    assert await _run(factory, vendor)
    with pytest.raises(ScheduleQueryNotExecutedError):  # 失败的检索式不算执行过
        await sch.upsert_schedule(db, owner, rid, _sched("TTL:(cup)", "TTL:(bad"))
    out = await sch.upsert_schedule(db, owner, rid, _sched("TTL:(cup)"))
    assert out["enabled"] and out["next_run_at"] > datetime.now(UTC)
    with pytest.raises(ResearchNotFoundError):
        await sch.upsert_schedule(db, other, rid, _sched("TTL:(cup)"))
    assert [i["research_id"] for i in (await sch.list_schedules(db, owner))["items"]] == [rid]
    assert (await sch.list_schedules(db, other))["items"] == []
    listed = await svc.list_researches(db, owner, page=1, page_size=20)
    assert listed["items"][0]["schedule_enabled"] is True
    await sch.delete_schedule(db, owner, rid)
    with pytest.raises(ScheduleNotFoundError):
        await sch.delete_schedule(db, owner, rid)


async def test_due_schedule_enqueues_once_then_skips_when_busy_or_over_budget(db, factory, vendor, monkeypatch):
    from app.table.opportunity import schedule_service as sch
    # run_due 处理全库到期的定时任务；库里别的（例如联调留下的）先停用，随测试事务回滚
    await db.execute(update(OppIpSchedule).values(enabled=False))
    await db.commit()
    owner = await _user(db)
    rid = await _research(db, owner)
    await svc.request_search(db, owner, rid, _search("TTL:(cup)"))
    assert await _run(factory, vendor)
    s = await sch.upsert_schedule(db, owner, rid, _sched("TTL:(cup)"))
    due = s["next_run_at"]
    later = due + timedelta(minutes=1)

    # 到期：入队一个标记为定时复检的检索任务；同一时刻再处理不会产生第二个
    assert await sch.run_due(factory, now=later) == 1
    assert await sch.run_due(factory, now=later) == 0
    d = await svc.get_research(db, owner, rid)
    assert d["active_task"]["kind"] == "search" and d["active_task"]["scheduled"] is True
    assert d["schedule"]["last_status"] == "queued" and d["schedule"]["last_task_id"] == d["active_task"]["id"]
    assert d["schedule"]["next_run_at"] == due + timedelta(days=7)

    # 下一次到期时上一个还在途：跳过并记原因，下次时间照样推进
    nxt = d["schedule"]["next_run_at"] + timedelta(minutes=1)
    assert await sch.run_due(factory, now=nxt) == 1
    d = await svc.get_research(db, owner, rid)
    assert d["schedule"]["last_status"] == "skipped" and "已有任务" in d["schedule"]["last_reason"]
    assert d["schedule"]["next_run_at"] == due + timedelta(days=14)
    assert await _run(factory, vendor)  # 执行定时那一次

    # 预算不足：跳过，不入队
    monkeypatch.setattr(settings, "OPPORTUNITY_IP_MCP_BUDGET_PER_RESEARCH", await _mcp_calls(db, rid))
    tasks_before = len((await svc.get_research(db, owner, rid))["tasks"])
    assert await sch.run_due(factory, now=due + timedelta(days=14, minutes=1)) == 1
    d = await svc.get_research(db, owner, rid)
    assert len(d["tasks"]) == tasks_before and d["active_task"] is None
    assert d["schedule"]["last_status"] == "skipped" and "预算不足" in d["schedule"]["last_reason"]

    # 暂停的不会到期
    await sch.upsert_schedule(db, owner, rid, _sched("TTL:(cup)", enabled=False))
    assert await sch.run_due(factory, now=due + timedelta(days=400)) == 0
