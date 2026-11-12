"""机会档案的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（specs/opportunity-dossier）：
- 建档：按站点解析关键词，解析不到的词保留并标记，不丢、不造 keyword_id；从词库分组建档只认自己的分组；
- ABA 快照：在榜期数与缺榜分开、7 天前当日不在榜则对比为空、在榜不足 7 期标记、点击第一更换次数、
  跨词共同商品；同一批数据重复刷新不追加；
- 人工证据只能作废，缺口恒为未接入；
- 决定：补证/观察必须带复查日期、无证据不能推进、状态与复查日期随最新决定、待复查筛选；
- 复查：相对上次决定所依据快照的变化；
- 团队可见与接手；IP 调研只能挂自己的，摘要对团队可见而明细仍 404；
- 追问：未配置 422、在途 409、成功/失败/未知分开、越界引用丢弃、租约过期转 unknown。
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

from app.table.opportunity import dossier_ask as ask
from app.table.opportunity import dossier_service as svc
from app.table.opportunity import research_service
from app.table.opportunity.dossier_errors import (
    AskInProgressError,
    AskNotConfiguredError,
    DecisionRuleError,
    DossierInputError,
    DossierNotFoundError,
    EvidenceNotFoundError,
    KeywordGroupNotFoundError,
    LastKeywordError,
)
from app.table.opportunity.dossier_model import OppDossierEvidence, OppDossierMessage
from app.table.opportunity.dossier_schemas import (
    AskCreate,
    DecisionCreate,
    DossierCreate,
    DossierUpdate,
    ManualEvidenceCreate,
)
from app.table.opportunity.ip_errors import ResearchNotFoundError
from app.table.opportunity.ip_model import OppIpReview
from app.table.opportunity.ip_schemas import ResearchCreate
from app.table.opportunity.llm_client import LlmClient
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

# 用 DE：夹具在事务内先清掉该站点的 site_data_stats，断言的期数只取决于本文件写入的行。
SITE = "DE"
TODAY = svc.today()
#: D[0] 是今天，D[i] 是 i 天前
D = [TODAY - timedelta(days=i) for i in range(12)]


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _ensure_partitions_sync() -> bool:
    from sqlalchemy import create_engine

    from app.table.analysis.partitioning import ensure_fact_partitions

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            if conn.execute(text("SELECT to_regclass('analysis.opp_dossier')")).scalar() is None:
                return False
            for d in D:
                ensure_fact_partitions(conn, "fact_daily", SITE, d)
                ensure_fact_partitions(conn, "fact_dropped", SITE, d)
        return True
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "sk-llm-test")
    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_MODEL", "test-model")


@pytest.fixture()
async def conn():
    if not _ensure_partitions_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    try:
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


def _tok() -> str:
    return uuid.uuid4().hex


async def _user(db, name="u") -> int:
    uid = (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,false) RETURNING id"), {"n": f"dossier-{name}-{uuid.uuid4().hex[:6]}"})).scalar()
    await db.commit()
    return uid


async def _product(db, title: str) -> tuple[int, str]:
    asin = "B0" + uuid.uuid4().hex[:8].upper()
    pid = (await db.execute(text(
        "INSERT INTO analysis.dim_product(asin,title) VALUES (:a,:t) RETURNING product_id"),
        {"a": asin, "t": title})).scalar()
    return pid, asin


async def _fact(db, kid: int, d: date, rank: int, tops: list[tuple[int, float, float]] | None = None) -> None:
    cols = {"s": SITE, "d": d, "k": kid, "r": rank}
    names, vals = "site,report_date,keyword_id,current_ranking", ":s,:d,:k,:r"
    for n, (pid, click, conv) in enumerate(tops or [], start=1):
        names += f",top{n}_product_id,top{n}_click_share,top{n}_conversion_share"
        vals += f",:p{n},:c{n},:v{n}"
        cols |= {f"p{n}": pid, f"c{n}": click, f"v{n}": conv}
    await db.execute(text(f"INSERT INTO analysis.fact_daily({names}) VALUES ({vals})"), cols)


async def _stats(db, days: list[date]) -> None:
    for d in days:
        await db.execute(text(
            "INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) VALUES (:s,'DAY',:d,1) "
            "ON CONFLICT DO NOTHING"), {"s": SITE, "d": d})


class Seed:
    steady = f"dossier steady {uuid.uuid4().hex[:6]}"
    gappy = f"dossier gappy {uuid.uuid4().hex[:6]}"
    unknown = f"dossier never listed {uuid.uuid4().hex[:6]}"


async def _seed(db) -> dict:
    """已导入 D[10]..D[1] 共 10 期（今天 D[0] 尚未导入）。

    - steady：10 期全在榜，排名从 1900 逐日升到 1000；点击第一前 5 期是商品 A、后 5 期是 B；
      最新期前三是 B / A / C。
    - gappy：只在 D[2]、D[1] 两期在榜（在榜不足 7 期）；最新期前三含 A（与 steady 共同）。
    - unknown：不在 dim_keyword。
    """
    await db.execute(text("DELETE FROM analysis.site_data_stats WHERE site=:s"), {"s": SITE})
    out: dict = {}
    for key in ("steady", "gappy"):
        out[key] = (await db.execute(text(
            "INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": getattr(Seed, key)})).scalar()
    (pa, out["A"]), (pb, out["B"]), (pc, out["C"]) = [await _product(db, f"Product {x}") for x in "ABC"]
    out["pids"] = (pa, pb, pc)
    for i in range(10, 0, -1):
        tops = [(pa, 30.0, 10.0), (pb, 20.0, 5.0)] if i > 5 else [(pb, 25.5, 12.0), (pa, 18.0, 0.0), (pc, 6.5, 3.0)]
        await _fact(db, out["steady"], D[i], 1000 + (i - 1) * 100, tops)
    for i in (2, 1):
        await _fact(db, out["gappy"], D[i], 50_000 - i * 1000, [(pc, 9.0, 0.0), (pa, 8.0, 1.0)])
    await _stats(db, D[1:11])
    await db.commit()
    return out


def _create(**kw) -> DossierCreate:
    base = dict(title="测试档案", idea="一个想法", site=SITE,
                keywords=[Seed.steady, f"  {Seed.gappy.upper()}  ", Seed.unknown], client_token=_tok())
    base.update(kw)
    return DossierCreate(**base)


def _kw(detail: dict, keyword: str) -> dict:
    return next(k for k in detail["snapshot"]["payload"]["keywords"] if k["keyword"] == keyword)


# ---------------------------------------------------------------- 建档与快照


async def test_create_resolves_keywords_and_builds_snapshot(db):
    seed = await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    detail = await svc.get_dossier(db, owner, did)

    assert detail["dossier"]["status"] == "researching"
    assert detail["dossier"]["site"] == SITE
    by_kw = {k["keyword"]: k for k in detail["keywords"]}
    # 大小写与空白已规范化；解析不到的词保留并标记
    assert set(by_kw) == {Seed.steady, Seed.gappy, Seed.unknown}
    assert by_kw[Seed.steady]["keyword_id"] == seed["steady"]
    assert by_kw[Seed.unknown]["resolved"] is False and by_kw[Seed.unknown]["keyword_id"] is None

    assert detail["snapshot_count"] == 1
    assert detail["snapshot"]["anchor_day"] == D[1]
    steady = _kw(detail, Seed.steady)
    assert steady["day"]["ranked_periods"] == 10 and steady["day"]["imported_periods"] == 10
    assert steady["day"]["latest"] == {"date": D[1].isoformat(), "ranking": 1000}
    assert steady["day"]["best"]["ranking"] == 1000
    assert steady["day"]["rank_7d_ago"] == {"date": D[8].isoformat(), "ranking": 1700}
    assert steady["thin"] is False
    assert [p["asin"] for p in steady["top3"]["products"]] == [seed["B"], seed["A"], seed["C"]]
    assert steady["top3"]["products"][0]["click_share"] == 25.5
    assert steady["top3"]["click_share_sum"] == 50.0
    assert steady["top1_churn"]["distinct_top1"] == 2 and steady["top1_churn"]["ranked_periods"] == 10

    gappy = _kw(detail, Seed.gappy)
    # 断续在榜：2 期在榜、10 期已导入；缺榜的期次是「已导入未收录」，不是排名 0
    assert gappy["day"]["ranked_periods"] == 2 and gappy["day"]["imported_periods"] == 10
    assert gappy["thin"] is True
    assert gappy["day"]["rank_7d_ago"] is None
    statuses = {p[0]: (p[1], p[2]) for p in gappy["day"]["points"]}
    assert statuses[D[5].isoformat()] == ("N", None)

    unknown = _kw(detail, Seed.unknown)
    assert unknown == {"keyword": Seed.unknown, "keyword_id": None, "resolved": False}

    shared = detail["snapshot"]["payload"]["shared_asins"]
    assert {s["asin"] for s in shared} == {seed["A"], seed["C"]}
    assert all(sorted(s["keywords"]) == sorted([Seed.steady, Seed.gappy]) for s in shared)
    # 快照不含评分或未接入的数值
    assert "score" not in json.dumps(detail["snapshot"]["payload"])


async def test_create_is_idempotent_and_rejects_bad_site(db):
    await _seed(db)
    owner = await _user(db)
    req = _create()
    first = await svc.create_dossier(db, owner, req)
    assert await svc.create_dossier(db, owner, req) == first
    with pytest.raises(DossierInputError):
        await svc.create_dossier(db, owner, _create(site="UK"))


async def test_refresh_does_not_append_without_new_data_then_appends(db):
    seed = await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    again = await svc.refresh_snapshot(db, owner, did)
    assert again["created"] is False
    assert (await svc.get_dossier(db, owner, did))["snapshot_count"] == 1

    # 新的一期（今天）导入后，刷新才追加
    await _fact(db, seed["steady"], D[0], 800, [(seed["pids"][2], 40.0, 20.0)])
    await _stats(db, [D[0]])
    await db.commit()
    detail = await svc.get_dossier(db, owner, did)
    assert detail["snapshot_stale"] == {"new_data": True, "keywords_changed": False}
    assert (await svc.refresh_snapshot(db, owner, did))["created"] is True
    detail = await svc.get_dossier(db, owner, did)
    assert detail["snapshot_count"] == 2 and detail["snapshot"]["anchor_day"] == D[0]
    # 没有决定时，基线是上一份快照
    ch = detail["changes"]
    assert ch["basis"] == "previous" and ch["periods_unchanged"] is False
    row = next(r for r in ch["keywords"] if r["keyword"] == Seed.steady)
    assert row["before"]["latest"]["ranking"] == 1000 and row["after"]["latest"]["ranking"] == 800
    assert row["before"]["ranked_periods"] == 10 and row["after"]["ranked_periods"] == 11
    assert row["top1_changed"] is True


async def test_keywords_add_remove_and_late_resolution(db):
    seed = await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create(keywords=[Seed.unknown])))["id"]
    detail = await svc.get_dossier(db, owner, did)
    only = detail["keywords"][0]
    with pytest.raises(LastKeywordError):
        await svc.remove_keyword(db, owner, did, only["id"])

    added = await svc.add_keywords(db, owner, did, [Seed.steady, Seed.unknown])
    assert added == {"added": 1, "unresolved": []}
    assert (await svc.get_dossier(db, owner, did))["snapshot_count"] == 2

    # 之前解析不到的词后来进了榜：下一次快照重新解析
    kid = (await db.execute(text(
        "INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
        {"s": SITE, "k": Seed.unknown})).scalar()
    await _fact(db, kid, D[1], 77_000)
    await db.commit()
    assert (await svc.refresh_snapshot(db, owner, did))["created"] is True
    detail = await svc.get_dossier(db, owner, did)
    assert next(k for k in detail["keywords"] if k["keyword"] == Seed.unknown)["keyword_id"] == kid
    assert _kw(detail, Seed.unknown)["day"]["latest"]["ranking"] == 77_000
    assert seed["steady"]


async def test_create_from_keyword_group_only_own(db):
    seed = await _seed(db)
    owner, other = await _user(db), await _user(db, "o")
    gid = (await db.execute(text(
        "INSERT INTO analysis.keyword_group(user_id,site,name) VALUES (:u,:s,'组') RETURNING group_id"),
        {"u": owner, "s": SITE})).scalar()
    for kid in (seed["steady"], seed["gappy"]):
        await db.execute(text("INSERT INTO analysis.keyword_group_item(group_id,keyword_id) VALUES (:g,:k)"),
                         {"g": gid, "k": kid})
    await db.commit()

    req = DossierCreate(title="从分组", keyword_group_id=gid, client_token=_tok())
    did = (await svc.create_dossier(db, owner, req))["id"]
    detail = await svc.get_dossier(db, owner, did)
    assert detail["dossier"]["site"] == SITE
    assert {k["keyword"] for k in detail["keywords"]} == {Seed.steady, Seed.gappy}

    with pytest.raises(KeywordGroupNotFoundError):
        await svc.create_dossier(db, other, DossierCreate(title="x", keyword_group_id=gid, client_token=_tok()))


# ---------------------------------------------------------------- 人工证据与决定


def _manual(**kw) -> ManualEvidenceCreate:
    base = dict(category="sales", content="月销约 3000 件", source_name="卖家精灵网页", observed_date=TODAY)
    base.update(kw)
    return ManualEvidenceCreate(**base)


async def test_manual_evidence_and_void_keep_gap_unconnected(db):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    eid = (await svc.add_manual_evidence(db, owner, did, _manual()))["id"]
    detail = await svc.get_dossier(db, owner, did)
    sales = next(g for g in detail["gaps"] if g["category"] == "sales")
    assert sales["connected"] is False and len(sales["evidence"]) == 1
    assert sales["evidence"][0]["source_name"] == "卖家精灵网页" and sales["evidence"][0]["voided_at"] is None
    assert [g["category"] for g in detail["gaps"]] == [
        "search_volume", "sales", "price", "bsr", "reviews", "supply_cost", "trademark"]

    await svc.void_manual_evidence(db, owner, did, eid)
    sales = next(g for g in (await svc.get_dossier(db, owner, did))["gaps"] if g["category"] == "sales")
    assert sales["evidence"][0]["voided_at"] is not None and sales["evidence"][0]["voided_by_name"]

    with pytest.raises(DossierInputError):
        await svc.add_manual_evidence(db, owner, did, _manual(observed_date=TODAY + timedelta(days=1)))
    # ABA 快照不是人工证据，不能被作废
    snap_id = (await svc.get_dossier(db, owner, did))["snapshot"]["id"]
    with pytest.raises(EvidenceNotFoundError):
        await svc.void_manual_evidence(db, owner, did, snap_id)


def test_manual_evidence_rejects_non_http_url():
    with pytest.raises(ValueError):
        _manual(source_url="javascript:alert(1)")
    assert _manual(source_url="https://example.com/x").source_url == "https://example.com/x"


def _decision(verdict: str, **kw) -> DecisionCreate:
    base = dict(verdict=verdict, reason="理由", client_token=_tok())
    base.update(kw)
    return DecisionCreate(**base)


async def test_decision_rules_and_status(db):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]

    for verdict in ("watch", "need_evidence"):
        with pytest.raises(DecisionRuleError):
            await svc.add_decision(db, owner, did, _decision(verdict))
    with pytest.raises(DecisionRuleError):
        await svc.add_decision(db, owner, did, _decision("watch", recheck_date=TODAY - timedelta(days=1)))

    req = _decision("watch", recheck_date=TODAY, next_step="一周后看是否还在榜")
    first = await svc.add_decision(db, owner, did, req)
    assert await svc.add_decision(db, owner, did, req) == first  # 幂等
    detail = await svc.get_dossier(db, owner, did)
    assert detail["dossier"]["status"] == "watch" and detail["dossier"]["recheck_date"] == TODAY
    d0 = detail["decisions"][0]
    assert d0["snapshot_id"] == detail["snapshot"]["id"]
    # 专利未核查不阻塞，但签署记录写明当时状态
    assert d0["ip_status"] == {"state": "unchecked", "research_count": 0, "reviews": []}

    # 待复查筛选：复查日期不晚于今天
    due = await svc.list_dossiers(db, owner, page=1, page_size=20, due=True)
    assert did in [i["id"] for i in due["items"]] and due["due_count"] >= 1

    await svc.add_decision(db, owner, did, _decision("advance"))
    detail = await svc.get_dossier(db, owner, did)
    assert detail["dossier"]["status"] == "advance" and detail["dossier"]["recheck_date"] is None
    assert [x["verdict"] for x in detail["decisions"]] == ["advance", "watch"]
    not_due = await svc.list_dossiers(db, owner, page=1, page_size=20, due=True)
    assert did not in [i["id"] for i in not_due["items"]]


async def test_advance_requires_some_evidence(db):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create(keywords=[Seed.unknown])))["id"]
    with pytest.raises(DecisionRuleError):
        await svc.add_decision(db, owner, did, _decision("advance"))
    # 否决不需要证据
    await svc.add_decision(db, owner, did, _decision("reject"))
    # 有了有效人工证据即可推进；作废后又不行
    eid = (await svc.add_manual_evidence(db, owner, did, _manual()))["id"]
    await svc.add_decision(db, owner, did, _decision("advance"))
    await svc.void_manual_evidence(db, owner, did, eid)
    with pytest.raises(DecisionRuleError):
        await svc.add_decision(db, owner, did, _decision("advance"))


async def test_changes_are_relative_to_last_decision_snapshot(db):
    seed = await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    await svc.add_decision(db, owner, did, _decision("watch", recheck_date=TODAY + timedelta(days=7)))
    # 决定所依据的就是最新快照：没有变化可显示
    assert (await svc.get_dossier(db, owner, did))["changes"] is None

    # 加词产生第二份快照（数据期次没变）
    await svc.add_keywords(db, owner, did, [f"dossier extra {uuid.uuid4().hex[:6]}"])
    ch = (await svc.get_dossier(db, owner, did))["changes"]
    assert ch["basis"] == "decision" and ch["periods_unchanged"] is True
    assert [r["state"] for r in ch["keywords"]].count("added") == 1

    # 新一期导入并刷新：基线仍是决定时的快照，而不是上一份
    await _fact(db, seed["gappy"], D[0], 30_000)
    await _stats(db, [D[0]])
    await db.commit()
    await svc.refresh_snapshot(db, owner, did)
    detail = await svc.get_dossier(db, owner, did)
    ch = detail["changes"]
    assert detail["snapshot_count"] == 3
    assert ch["base_snapshot_id"] == detail["decisions"][0]["snapshot_id"] and ch["periods_unchanged"] is False
    row = next(r for r in ch["keywords"] if r["keyword"] == Seed.gappy)
    assert (row["before"]["ranked_periods"], row["after"]["ranked_periods"]) == (2, 3)
    assert row["after"]["latest"] == {"date": D[0].isoformat(), "ranking": 30_000}


# ---------------------------------------------------------------- 团队可见、接手、归档


async def test_team_visible_take_over_and_archive(db):
    await _seed(db)
    owner, other = await _user(db), await _user(db, "o")
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    detail = await svc.get_dossier(db, other, did)
    assert detail["dossier"]["creator_id"] == owner and detail["dossier"]["assignee_id"] == owner

    await svc.update_dossier(db, other, did, DossierUpdate(take_over=True))
    await svc.add_manual_evidence(db, other, did, _manual(category="price", content="竞品标价 19.99"))
    detail = await svc.get_dossier(db, owner, did)
    assert detail["dossier"]["assignee_id"] == other and detail["dossier"]["creator_id"] == owner
    price = next(g for g in detail["gaps"] if g["category"] == "price")
    assert price["evidence"][0]["created_by"] == other

    mine = await svc.list_dossiers(db, other, page=1, page_size=20, mine=True)
    assert did in [i["id"] for i in mine["items"]]
    assert did not in [i["id"] for i in (await svc.list_dossiers(db, owner, page=1, page_size=20, mine=True))["items"]]

    await svc.update_dossier(db, owner, did, DossierUpdate(archived=True))
    assert did not in [i["id"] for i in (await svc.list_dossiers(db, owner, page=1, page_size=50))["items"]]
    assert did in [i["id"] for i in (await svc.list_dossiers(db, owner, page=1, page_size=50, archived=True))["items"]]
    await svc.update_dossier(db, owner, did, DossierUpdate(archived=False))
    found = await svc.list_dossiers(db, owner, page=1, page_size=50, q=Seed.gappy[-6:])
    assert [i["id"] for i in found["items"]] == [did]

    with pytest.raises(DossierNotFoundError):
        await svc.get_dossier(db, owner, 2_000_000_000)


# ---------------------------------------------------------------- 关联 IP 调研


async def test_ip_research_link_summary_visible_detail_private(db):
    await _seed(db)
    owner, other = await _user(db), await _user(db, "o")
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    rid = (await research_service.create_research(db, owner, ResearchCreate(
        client_token=_tok(), product_name="迷你无线直发梳", description="便携", keywords=["hot brush"],
        loc_classes=["28-03"], jurisdictions=["US"], markets=["US", "DE"])))["id"]

    # 只能关联自己名下的调研
    with pytest.raises(ResearchNotFoundError):
        await svc.link_research(db, other, did, rid)
    await svc.link_research(db, owner, did, rid)

    ip = (await svc.get_dossier(db, other, did))["ip"]
    # 没有人工意见：未核查，不是低风险
    assert ip["state"] == "unchecked"
    assert ip["researches"][0]["product_name"] == "迷你无线直发梳" and ip["researches"][0]["owner_id"] == owner
    assert ip["researches"][0]["reviews"] == []

    db.add(OppIpReview(research_id=rid, market="US", risk_level="medium", decision="continue_verify", basis="b",
                       uncovered_scope="u", evidence_candidate_ids=[], signer_id=owner, client_token=_tok(),
                       input_fingerprint="f"))
    await db.commit()
    ip = (await svc.get_dossier(db, other, did))["ip"]
    assert ip["state"] == "reviewed"
    assert [(m["market"], m["risk_level"], m["decision"]) for m in ip["researches"][0]["reviews"]] == [
        ("US", "medium", "continue_verify")]
    # 摘要之外的明细仍只有创建人能看
    with pytest.raises(ResearchNotFoundError):
        await research_service.get_research(db, other, rid)

    await svc.add_decision(db, other, did, _decision("advance"))
    d0 = (await svc.get_dossier(db, other, did))["decisions"][0]
    assert d0["ip_status"]["state"] == "reviewed"
    assert d0["ip_status"]["reviews"] == [
        {"research_id": rid, "market": "US", "risk_level": "medium", "decision": "continue_verify"}]

    linkable = await svc.my_linkable_researches(db, owner)
    assert {"id": rid, "dossier_id": did} == {k: linkable["items"][0][k] for k in ("id", "dossier_id")}
    with pytest.raises(ResearchNotFoundError):
        await svc.unlink_research(db, other, did, rid)
    await svc.unlink_research(db, owner, did, rid)
    assert (await svc.get_dossier(db, owner, did))["ip"] == {"state": "unchecked", "researches": []}


# ---------------------------------------------------------------- 追问


class FakeLlm:
    def __init__(self, reply=None, status=200):
        self.calls: list[dict] = []
        self.reply, self.status = reply, status

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(req.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(self.reply)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    def __call__(self) -> LlmClient:
        return LlmClient(base_url="https://llm.example.com/v1", api_key="k", model="test-model", timeout=5,
                         transport=httpx.MockTransport(self.handler))


async def _last_message(db, did: int) -> dict:
    return (await svc.get_dossier(db, 0, did))["messages"][-1]


async def test_ask_requires_llm_and_blocks_second_in_flight(db, monkeypatch):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]

    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "")
    with pytest.raises(AskNotConfiguredError):
        await ask.request_ask(db, owner, did, AskCreate(question="头部稳定吗"))
    # 大模型未配置不影响其余功能
    assert (await svc.get_dossier(db, owner, did))["llm_configured"] is False
    await svc.add_manual_evidence(db, owner, did, _manual())

    monkeypatch.setattr(settings, "OPPORTUNITY_LLM_API_KEY", "sk-llm-test")
    queued = await ask.request_ask(db, owner, did, AskCreate(question="头部稳定吗"))
    assert queued["status"] == "queued"
    with pytest.raises(AskInProgressError):
        await ask.request_ask(db, owner, did, AskCreate(question="再问一个"))
    assert (await svc.get_dossier(db, owner, did))["asking"] is True


async def test_ask_answers_from_dossier_evidence_and_drops_foreign_citations(db, factory):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    manual_id = (await svc.add_manual_evidence(db, owner, did, _manual()))["id"]
    voided_id = (await svc.add_manual_evidence(db, owner, did, _manual(content="已作废的说法")))["id"]
    await svc.void_manual_evidence(db, owner, did, voided_id)
    snap_id = (await svc.get_dossier(db, owner, did))["snapshot"]["id"]

    await ask.request_ask(db, owner, did, AskCreate(question="头部稳定吗"))
    llm = FakeLlm({"answer": "近 30 天点击第一换过 2 个商品。",
                   "cited_evidence_ids": [snap_id, manual_id, voided_id, 999_999_999, "7", True]})
    assert await ask.run_one(factory, llm) is True
    assert await ask.run_one(factory, llm) is False  # 队列已空

    msg = await _last_message(db, did)
    assert msg["status"] == "completed" and msg["model"] == "test-model"
    # 已作废的证据、别处的 id、非整数都被丢弃
    assert msg["cited_evidence_ids"] == [snap_id, manual_id]
    assert (await svc.get_dossier(db, owner, did))["asking"] is False

    sent = llm.calls[0]["messages"]
    assert "不得估算或编造销量" in sent[0]["content"] and "未核查" in sent[0]["content"]
    user_prompt = sent[1]["content"]
    assert "<dossier_evidence>" in user_prompt and Seed.steady in user_prompt
    assert "月销约 3000 件" in user_prompt and "已作废的说法" not in user_prompt
    assert '"status": "未接入"' in user_prompt

    # 第二问带上上一轮问答
    await ask.request_ask(db, owner, did, AskCreate(question="那转化呢"))
    await ask.run_one(factory, llm)
    assert "头部稳定吗" in llm.calls[1]["messages"][1]["content"]


@pytest.mark.parametrize(("reply", "status", "expected", "category"), [
    ({"answer": "", "cited_evidence_ids": []}, 200, "failed", "invalid_output"),
    ({"text": "没有 answer 字段"}, 200, "failed", "invalid_output"),
    (None, 401, "failed", "credential_invalid"),
    (None, 503, "unknown", "upstream_5xx"),
])
async def test_ask_failure_and_unknown_are_distinct(db, factory, reply, status, expected, category):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    await ask.request_ask(db, owner, did, AskCreate(question="q"))
    await ask.run_one(factory, FakeLlm(reply, status))
    msg = await _last_message(db, did)
    assert (msg["status"], msg["error_category"], msg["answer"]) == (expected, category, None)
    # 不自动重发；用户可以再问
    assert await ask.run_one(factory, FakeLlm({"answer": "x"})) is False
    await ask.request_ask(db, owner, did, AskCreate(question="再问"))


async def test_ask_lease_expiry_becomes_unknown_and_late_result_is_ignored(db, factory):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    mid = (await ask.request_ask(db, owner, did, AskCreate(question="q")))["id"]
    await db.execute(update(OppDossierMessage).where(OppDossierMessage.id == mid).values(
        status="running", lease_until=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()
    assert await ask.reap_expired(factory) == 1
    async with factory() as s:
        await ask._finish(s, mid, status="completed", answer="迟到的回答")
    msg = await _last_message(db, did)
    assert (msg["status"], msg["error_category"], msg["answer"]) == ("unknown", "interrupted", None)


async def test_snapshots_are_append_only_rows(db):
    await _seed(db)
    owner = await _user(db)
    did = (await svc.create_dossier(db, owner, _create()))["id"]
    await svc.add_keywords(db, owner, did, [f"dossier extra {uuid.uuid4().hex[:6]}"])
    rows = (await db.execute(select(func.count()).select_from(OppDossierEvidence).where(
        OppDossierEvidence.dossier_id == did, OppDossierEvidence.kind == "aba_snapshot"))).scalar_one()
    assert rows == 2
