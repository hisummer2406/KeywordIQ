"""机会档案的 ABA 证据快照（change add-opportunity-dossier 决策 3）。

快照是**确定性**的：只读 ABA 事实，同一批数据算出同一份内容。它只陈述报表里有的事实——
排名、在榜与否、点击前三商品及份额——不含销量、搜索量、机会评分或「值得做」一类结论。

口径不在这里重写：

- 逐期五态与首次证据来自 ``trend_service.get_keyword_trend``（排名趋势详情的同一实现）；
- 点击前三商品、品牌、类目的投影来自 ``analysis_crud.fact_metric_columns``。品牌与类目是
  **关键词级**的前三排行，与商品槽位无关，快照里分开存放，不把第 N 个品牌配给第 N 个商品。

只按 ``(site, keyword_id, 日期列)`` 索引读事实表，每个词约 6 条查询；词数上限见
``dossier_model.MAX_KEYWORDS``。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.table.analysis import analysis_crud
from app.table.analysis.dimension_model import DimKeyword, DimProduct
from app.table.keyword import trend_service
from app.table.keyword.trend_schemas import KeywordTrendResponse

SNAPSHOT_VERSION = 1
#: 在榜不足这么多期（日榜）的词不判断趋势
THIN_RANKED_PERIODS = 7
#: 点击第一商品更换次数的观察窗口（日历天）
TOP1_WINDOW_DAYS = 30

#: 五态 → 快照里的单字母编码（逐期序列要存几十个点，用短码压体积）
_STATUS_CODE = {"RANKED": "R", "DROPPED": "D", "NO_DATA": "N", "NOT_IMPORTED": "X", "OUT_OF_RETENTION": "O"}
#: 「该期报表已导入」的状态：在榜、掉榜、已导入未收录
_IMPORTED = {"R", "D", "N"}


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _series(trend: KeywordTrendResponse) -> dict[str, Any]:
    """一个视图（日榜或周榜）的逐期序列与统计。"""
    points = [[p.date.isoformat(), _STATUS_CODE[p.status], p.ranking] for p in trend.points]
    ranked = sum(1 for _, s, _ in points if s == "R")
    imported = sum(1 for _, s, _ in points if s in _IMPORTED)
    stat = lambda s: {"date": s.date.isoformat(), "ranking": s.ranking} if s else None  # noqa: E731
    return {
        "anchor": _iso(trend.on),
        "points": points,
        "ranked_periods": ranked,
        "imported_periods": imported,
        "best": stat(trend.best_ranking),
        "latest": stat(trend.latest_available),
    }


def _rank_7d_ago(day: dict[str, Any]) -> dict[str, Any] | None:
    """最新在榜期 d 与 d-7 当天的排名。d-7 当天不在榜即为空——不用邻近日期代替。"""
    latest = day["latest"]
    if not latest:
        return None
    target = (date.fromisoformat(latest["date"]) - timedelta(days=7)).isoformat()
    for d, status, ranking in day["points"]:
        if d == target:
            return {"date": d, "ranking": ranking} if status == "R" else None
    return None


async def _top3(db: AsyncSession, site: str, keyword_id: int, on: date) -> dict[str, Any] | None:
    """最新在榜日的点击前三商品、关键词级前三品牌与类目。投影复用市场分析的唯一定义。"""
    fact, date_col = analysis_crud.fact_and_datecol("DAY")
    dims = analysis_crud.new_fact_row_dims()
    stmt = (
        select(*analysis_crud.fact_metric_columns(fact, dims, site=site, view="DAY", on=on))
        .select_from(fact)
        .join(DimKeyword, DimKeyword.keyword_id == fact.keyword_id)
        .where(fact.site == site, date_col == on, fact.keyword_id == keyword_id)
    )
    row = (await db.execute(analysis_crud.join_fact_row_dims(stmt, fact, dims))).mappings().first()
    if row is None:
        return None
    products = [
        {"slot": n, "asin": row[f"top{n}_asin"], "title": row[f"top{n}_title"] or None,
         "click_share": _f(row[f"top{n}_click_share"]), "conversion_share": _f(row[f"top{n}_conversion_share"])}
        for n in (1, 2, 3) if row[f"top{n}_asin"]
    ]
    return {
        "date": on.isoformat(),
        "ranking": row["current_ranking"],
        "products": products,
        "click_share_sum": round(sum(p["click_share"] or 0 for p in products), 2) if products else None,
        "brands": [b for b in (row["top1_brand"], row["top2_brand"], row["top3_brand"]) if b],
        "categories": [c for c in (row["top1_category"], row["top2_category"], row["top3_category"]) if c],
    }


async def _top1_churn(db: AsyncSession, site: str, keyword_id: int, end: date) -> dict[str, Any]:
    """日榜近 30 天内，点击第一商品换过几个。只统计在榜的期次。"""
    fact, date_col = analysis_crud.fact_and_datecol("DAY")
    p1 = aliased(DimProduct)
    start = end - timedelta(days=TOP1_WINDOW_DAYS - 1)
    rows = (await db.execute(
        select(date_col, p1.asin)
        .select_from(fact)
        .join(p1, p1.product_id == fact.top1_product_id, isouter=True)
        .where(fact.site == site, fact.keyword_id == keyword_id, date_col >= start, date_col <= end)
        .order_by(date_col)
    )).all()
    asins = [a for _, a in rows if a]
    return {
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "ranked_periods": len(rows),
        "distinct_top1": len(set(asins)),
        "series": [[d.isoformat(), a] for d, a in rows],
    }


async def _keyword_evidence(db: AsyncSession, site: str, keyword: str, keyword_id: int) -> dict[str, Any]:
    day_trend = await trend_service.get_keyword_trend(
        db, keyword_id=keyword_id, site=site, view="DAY", range_="ALL", on=None)
    week_trend = await trend_service.get_keyword_trend(
        db, keyword_id=keyword_id, site=site, view="WEEK", range_="ALL", on=None)
    day, week = _series(day_trend), _series(week_trend)
    day["rank_7d_ago"] = _rank_7d_ago(day)

    top3 = churn = None
    if day["latest"]:
        latest = date.fromisoformat(day["latest"]["date"])
        top3 = await _top3(db, site, keyword_id, latest)
    if day["anchor"]:
        churn = await _top1_churn(db, site, keyword_id, date.fromisoformat(day["anchor"]))

    fe = day_trend.first_evidence
    return {
        "keyword": keyword, "keyword_id": keyword_id, "resolved": True,
        "day": day, "week": week,
        "first_evidence": fe.model_dump(mode="json") if fe else None,
        "top3": top3, "top1_churn": churn,
        # 在榜期数不足：只陈述事实，不判断趋势
        "thin": day["ranked_periods"] < THIN_RANKED_PERIODS,
    }


def _shared_asins(keywords: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在两个及以上关键词最新在榜期的点击前三里出现的 ASIN。"""
    seen: dict[str, dict[str, Any]] = {}
    for kw in keywords:
        for p in (kw.get("top3") or {}).get("products", []):
            entry = seen.setdefault(p["asin"], {"asin": p["asin"], "title": p["title"], "keywords": []})
            entry["title"] = entry["title"] or p["title"]
            entry["keywords"].append(kw["keyword"])
    shared = [e for e in seen.values() if len(e["keywords"]) >= 2]
    return sorted(shared, key=lambda e: (-len(e["keywords"]), e["asin"]))


async def latest_anchors(db: AsyncSession, site: str) -> tuple[date | None, date | None]:
    """该站点日榜、周榜已导入的最新期。"""
    out: list[date | None] = []
    for view in ("DAY", "WEEK"):
        out.append((await db.execute(analysis_crud.available_dates_stmt(site, view).limit(1))).scalar_one_or_none())
    return out[0], out[1]


def keyword_set(keywords: list[tuple[str, int | None]]) -> list[list[Any]]:
    """快照里记录的关键词集合（判断「关键词未变」用）。"""
    return sorted([kw, kid] for kw, kid in keywords)


async def build_snapshot(db: AsyncSession, site: str, keywords: list[tuple[str, int | None]],
                         anchors: tuple[date | None, date | None]) -> dict[str, Any]:
    """为一组 ``(关键词, keyword_id 或 None)`` 生成快照内容。"""
    items: list[dict[str, Any]] = []
    for keyword, keyword_id in keywords:
        if keyword_id is None:
            # 不在 dim_keyword：保留期内未进入过该站点 ABA 榜单。不是 0，也不是「没有需求」。
            items.append({"keyword": keyword, "keyword_id": None, "resolved": False})
        else:
            items.append(await _keyword_evidence(db, site, keyword, keyword_id))
    return {
        "version": SNAPSHOT_VERSION,
        "site": site,
        "anchor_day": _iso(anchors[0]),
        "anchor_week": _iso(anchors[1]),
        "keyword_set": keyword_set(keywords),
        "keywords": items,
        "shared_asins": _shared_asins(items),
    }


def _summary(kw: dict[str, Any]) -> dict[str, Any]:
    if not kw.get("resolved"):
        return {"resolved": False, "latest": None, "ranked_periods": None, "top1_asin": None}
    products = (kw.get("top3") or {}).get("products") or []
    return {
        "resolved": True,
        "latest": kw["day"]["latest"],
        "ranked_periods": kw["day"]["ranked_periods"],
        "imported_periods": kw["day"]["imported_periods"],
        "top1_asin": products[0]["asin"] if products else None,
    }


def compare(base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """当前快照相对基线快照的变化（spec「复查时给出相对上次决定的变化」）。"""
    old = {k["keyword"]: _summary(k) for k in base["keywords"]}
    new = {k["keyword"]: _summary(k) for k in current["keywords"]}
    rows: list[dict[str, Any]] = []
    for kw in [k["keyword"] for k in current["keywords"]]:
        n, o = new[kw], old.get(kw)
        if o is None:
            rows.append({"keyword": kw, "state": "added", "before": None, "after": n})
            continue
        rows.append({
            "keyword": kw, "state": "kept", "before": o, "after": n,
            "top1_changed": bool(o["top1_asin"] and n["top1_asin"] and o["top1_asin"] != n["top1_asin"]),
        })
    rows += [{"keyword": kw, "state": "removed", "before": o, "after": None}
             for kw, o in old.items() if kw not in new]
    return {
        "base_anchor_day": base.get("anchor_day"), "base_anchor_week": base.get("anchor_week"),
        "anchor_day": current.get("anchor_day"), "anchor_week": current.get("anchor_week"),
        # 基线之后没有新导入：排名不会变，界面要明说，而不是显示一排「无变化」让人以为市场没动
        "periods_unchanged": (base.get("anchor_day") == current.get("anchor_day")
                              and base.get("anchor_week") == current.get("anchor_week")),
        "keywords": rows,
    }
