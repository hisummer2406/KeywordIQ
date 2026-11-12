"""机会中心线索——信号洞察专家（change add-opportunity-signal-leads）。

线索是**持续增长榜的只读视图**：榜单在导入时算定（``importer.load_growth``），这里只调用
``growth_service`` 取一页结果与口径，再标出每个词已经在哪些未归档的机会档案里。不新建表，
不复制判定条件——口径只有那一处定义。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.table.keyword import growth_service
from app.table.keyword.growth_schemas import GrowthSearchRequest
from app.table.opportunity.dossier_model import OppDossier, OppDossierKeyword


async def _dossiers_by_keyword(db: AsyncSession, site: str, keyword_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """当页关键词所属的未归档档案（同站点）。档案团队可见，不按用户过滤。

    已归档的不算：归档表示这件事已经放下，新一轮走强应当被重新看到。
    """
    if not keyword_ids:
        return {}
    rows = (await db.execute(
        select(OppDossierKeyword.keyword_id, OppDossier.id, OppDossier.title, OppDossier.status)
        .join(OppDossier, OppDossier.id == OppDossierKeyword.dossier_id)
        .where(OppDossierKeyword.keyword_id.in_(keyword_ids), OppDossier.site == site,
               OppDossier.archived_at.is_(None))
        .order_by(OppDossier.id))).all()
    out: dict[int, list[dict[str, Any]]] = {}
    for kid, did, title, status in rows:
        out.setdefault(kid, []).append({"id": did, "title": title, "status": status})
    return out


async def growth_leads(db: AsyncSession, *, site: str | None, on: date | None, page: int,
                       page_size: int) -> dict[str, Any]:
    """一页持续增长线索 + 判定口径 + 缺期 + 档案归属。非法站点或日期由 growth_service 报 422。"""
    scope = await growth_service.get_scope(db, site)
    result = await growth_service.search(
        db, GrowthSearchRequest(site=site, date=on, page=page, perPage=page_size))
    items = [row.model_dump(mode="json") for row in result.data]
    dossiers = await _dossiers_by_keyword(db, result.site, [it["keyword_id"] for it in items])
    for it in items:
        it["dossiers"] = dossiers.get(it["keyword_id"], [])
    return {
        "site": result.site, "date": result.date, "total": result.total, "page": page, "page_size": page_size,
        "items": items,
        # 窗口内未导入的期次：非空时榜单可能据实为空，界面必须明示
        "missing_dates": result.missing_dates,
        "available_dates": scope.available_dates,
        # 口径随响应下发，前端不硬编码
        "criteria": {"window_days": scope.window_days, "min_rank_gain": scope.min_rank_gain,
                     "min_daily_rate": scope.min_daily_rate},
    }
