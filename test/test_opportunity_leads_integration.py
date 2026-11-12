"""机会中心线索的服务层集成测试（真库）。默认跳过，只在设置 TEST_DATABASE_URL 时运行。

钉住的事（specs/opportunity-leads）：
- 线索只是持续增长榜的透传：行、走势、口径与缺期都来自 growth_service，不另行判定；
- 档案归属：同站点未归档的计入，已归档与别的站点的不计，不按用户过滤；
- 非法站点 422，不回退默认站点；端点挂 opportunity:view。
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.keyword.growth_service import GrowthScopeError
from app.table.opportunity import dossier_service
from app.table.opportunity import leads_service as svc
from app.table.opportunity.dossier_model import OppDossier, OppDossierKeyword
from app.table.opportunity.dossier_schemas import DossierCreate, DossierUpdate
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

# 用 GB：夹具在事务内先清掉该站点的 site_data_stats 与当日榜单，断言只取决于本文件写入的行。
SITE = "GB"
TODAY = dossier_service.today()
WINDOW = settings.GROWTH_WINDOW_DAYS
D = [TODAY - timedelta(days=i) for i in range(WINDOW + 1)]
#: 窗口内故意不导入的一期
MISSING = D[3]


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
                for table in ("fact_daily", "fact_dropped", "fact_growth"):
                    ensure_fact_partitions(conn, table, SITE, d)
        return True
    finally:
        engine.dispose()


@pytest.fixture()
async def db():
    if not _ensure_partitions_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


async def _user(db, name="u") -> int:
    uid = (await db.execute(text(
        "INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
        "VALUES (:n,'x',true,false) RETURNING id"), {"n": f"leads-{name}-{uuid.uuid4().hex[:6]}"})).scalar()
    await db.commit()
    return uid


async def _seed(db) -> dict[str, int]:
    """GB 站点：窗口内除 MISSING 外都已导入；锚定日榜单上有两个词（rising 净提升更大）。"""
    await db.execute(text("DELETE FROM analysis.site_data_stats WHERE site=:s"), {"s": SITE})
    await db.execute(text("DELETE FROM analysis.fact_growth WHERE site=:s AND anchor_date=:d"), {"s": SITE, "d": D[0]})
    tag = uuid.uuid4().hex[:6]
    ids: dict[str, int] = {}
    for key, start, end in (("rising", 120_000, 15_000), ("steady", 60_000, 40_000)):
        kw = f"leads {key} {tag}"
        kid = (await db.execute(text(
            "INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
            {"s": SITE, "k": kw})).scalar()
        ids[key], ids[f"{key}_text"] = kid, kw  # type: ignore[assignment]
        for i, d in enumerate(reversed(D[:WINDOW])):
            if d == MISSING:
                continue
            rank = start - (start - end) * i // (WINDOW - 1)
            await db.execute(text(
                "INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) VALUES (:s,:d,:k,:r)"),
                {"s": SITE, "d": d, "k": kid, "r": rank})
        await db.execute(text(
            "INSERT INTO analysis.fact_growth(site,anchor_date,keyword_id,start_ranking,end_ranking,rank_gain,"
            "min_growth_rate) VALUES (:s,:d,:k,:a,:b,:g,0.12)"),
            {"s": SITE, "d": D[0], "k": kid, "a": start, "b": end, "g": start - end})
    for d in D[:WINDOW]:
        if d != MISSING:
            await db.execute(text(
                "INSERT INTO analysis.site_data_stats(site,period_type,report_date,row_count) VALUES (:s,'DAY',:d,2)"),
                {"s": SITE, "d": d})
    await db.commit()
    return ids


async def test_leads_pass_through_growth_list_criteria_and_missing_dates(db):
    ids = await _seed(db)
    out = await svc.growth_leads(db, site="gb", on=None, page=1, page_size=50)

    assert (out["site"], out["date"], out["total"]) == (SITE, D[0], 2)
    # 默认按净提升降序，与增长榜一致
    assert [it["keyword_id"] for it in out["items"]] == [ids["rising"], ids["steady"]]
    first = out["items"][0]
    assert (first["start_ranking"], first["end_ranking"], first["rank_gain"]) == (120_000, 15_000, 105_000)
    assert first["trend"][-1] == 15_000 and len(first["trend"]) == WINDOW - 1
    assert first["dossiers"] == []
    # 缺期必须回传；口径来自配置，不在这里另算
    assert out["missing_dates"] == [MISSING]
    assert out["criteria"] == {"window_days": WINDOW, "min_rank_gain": settings.GROWTH_MIN_RANK_GAIN,
                               "min_daily_rate": settings.GROWTH_MIN_DAILY_RATE}
    assert D[0] in out["available_dates"] and MISSING not in out["available_dates"]

    page2 = await svc.growth_leads(db, site=SITE, on=D[0], page=2, page_size=1)
    assert [it["keyword_id"] for it in page2["items"]] == [ids["steady"]] and page2["total"] == 2


async def test_leads_mark_unarchived_dossiers_of_same_site_for_everyone(db):
    ids = await _seed(db)
    owner, other = await _user(db), await _user(db, "o")

    def req(title: str) -> DossierCreate:
        return DossierCreate(title=title, site=SITE, keywords=[ids["rising_text"]], client_token=uuid.uuid4().hex)

    active = (await dossier_service.create_dossier(db, owner, req("在研")))["id"]
    archived = (await dossier_service.create_dossier(db, owner, req("已放下")))["id"]
    await dossier_service.update_dossier(db, owner, archived, DossierUpdate(archived=True))
    # 别的站点的档案里即便挂着同一个 keyword_id 也不算
    foreign = OppDossier(title="别的站点", site="US", creator_id=owner, assignee_id=owner, client_token=uuid.uuid4().hex)
    db.add(foreign)
    await db.flush()
    db.add(OppDossierKeyword(dossier_id=foreign.id, keyword="x", keyword_id=ids["rising"], added_by=owner))
    await db.commit()

    # 档案团队可见：另一个用户看到的归属相同（线索查询本身不带用户）
    assert other != owner
    out = await svc.growth_leads(db, site=SITE, on=None, page=1, page_size=50)
    by_id = {it["keyword_id"]: it["dossiers"] for it in out["items"]}
    assert by_id[ids["rising"]] == [{"id": active, "title": "在研", "status": "researching"}]
    assert by_id[ids["steady"]] == []


async def test_leads_reject_unknown_site_and_date(db):
    await _seed(db)
    with pytest.raises(GrowthScopeError):
        await svc.growth_leads(db, site="UK", on=None, page=1, page_size=50)
    # 缺失站点不回退默认站点（PR #188 评审 P1）
    for missing in ("", "  ", None):
        with pytest.raises(GrowthScopeError):
            await svc.growth_leads(db, site=missing, on=None, page=1, page_size=50)  # type: ignore[arg-type]
    with pytest.raises(GrowthScopeError):
        await svc.growth_leads(db, site=SITE, on=MISSING, page=1, page_size=50)


async def test_leads_empty_site_is_empty_not_error(db):
    await db.execute(text("DELETE FROM analysis.site_data_stats WHERE site=:s"), {"s": SITE})
    await db.commit()
    out = await svc.growth_leads(db, site=SITE, on=None, page=1, page_size=50)
    assert (out["date"], out["total"], out["items"], out["missing_dates"]) == (None, 0, [], [])


async def test_dossier_list_filters_by_site_before_pagination(db):
    """并入目标按站点在服务端筛选：别的站点的档案再多，也不会把同站点的挤出第一页（PR #188 评审 P2）。"""
    ids = await _seed(db)
    owner = await _user(db)
    mine = (await dossier_service.create_dossier(db, owner, DossierCreate(
        title="GB 档案", site=SITE, keywords=[ids["rising_text"]], client_token=uuid.uuid4().hex)))["id"]
    # 之后再建三份别的站点的档案，它们的 updated_at 更晚，会排在前面
    for i in range(3):
        db.add(OppDossier(title=f"US 档案 {i}", site="US", creator_id=owner, assignee_id=owner,
                          client_token=uuid.uuid4().hex))
    await db.commit()
    first_page = await dossier_service.list_dossiers(db, owner, page=1, page_size=2, site=SITE)
    assert [d["id"] for d in first_page["items"]] == [mine] and first_page["total"] >= 1
    assert all(d["site"] == SITE for d in first_page["items"])
    from app.table.opportunity.dossier_errors import DossierInputError
    with pytest.raises(DossierInputError):
        await dossier_service.list_dossiers(db, owner, page=1, page_size=2, site="UK")


def test_leads_route_requires_site_param():
    from fastapi.routing import APIRoute

    from app.api_router import api_router

    route = next(r for r in api_router.routes
                 if isinstance(r, APIRoute) and r.path == "/api/opportunity/leads/growth")
    site = next(p for p in route.dependant.query_params if p.name == "site")
    assert site.required is True


def test_leads_route_requires_opportunity_view_only():
    from fastapi.routing import APIRoute

    from app.api_router import api_router
    from app.auth.permissions import KEYWORD_VIEW, OPPORTUNITY_VIEW

    def perms(dependant) -> set[str]:
        out: set[str] = set()
        for dep in dependant.dependencies:
            call = dep.call
            if getattr(call, "__qualname__", "").endswith("require_permission.<locals>._require"):
                out.update(c.cell_contents for c in call.__closure__ or () if isinstance(c.cell_contents, str))
            out |= perms(dep)
        return out

    route = next(r for r in api_router.routes
                 if isinstance(r, APIRoute) and r.path == "/api/opportunity/leads/growth")
    assert perms(route.dependant) == {OPPORTUNITY_VIEW}
    assert KEYWORD_VIEW not in perms(route.dependant)
