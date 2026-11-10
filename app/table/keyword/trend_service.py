"""关键词排名趋势详情的业务层。

职责：站点/keyword_id 校验、锚定期解析（含日/周切换的映射）、期望期次枚举、五态分类、
区间统计。查询本身在 `trend_crud` 里，纯只读、无事务边界可言（不写库）。

五态分类见 openspec 变更 `add-keyword-trend-detail` 的 design.md 决策 2：
超出保留窗口的期次 MUST NOT 依赖 `site_data_stats` 判定——保留清理只 DROP 事实表的月度
分区，从不清理 `site_data_stats` 的对应行，按"是否在 site_data_stats 里"判断"是否导入
过"会把已经物理删除的月份误判成"已导入但未收录"。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import (
    CODE_KEYWORD_SCOPE_INVALID,
    CODE_KEYWORD_TREND_NOT_FOUND,
    CODE_KEYWORD_TREND_SITE_MISMATCH,
    NotFoundError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.analysis import analysis_crud
from app.table.analysis.partitioning import retention_cutoff
from app.table.keyword import trend_crud as crud
from app.table.keyword.trend_schemas import (
    FirstEvidence,
    KeywordFirstEvidence,
    KeywordTrendCoverage,
    KeywordTrendPoint,
    KeywordTrendResponse,
    KeywordTrendStat,
    TrendPointStatus,
)
from config import settings

# 两个视图的期次步长（天）。周视图的 7 天不是自然周边界，是既有导入链路里
# "上一期偏移"就在用的同一个数（见 app/table/upload/importer.py 的 _PERIOD_CONFIG），
# previous_ranking/ranking_change 已经在用它，这里不新造概念。
_STEP_DAYS: dict[str, int] = {"DAY": 1, "WEEK": 7}

# 「近 30 天」的自然日历窗口，两个视图统一按日历天数取（周视图落进去自然只有约 4~5 个
# 整周期点，是预期行为，不是要凑够 30 个点）。
_RECENT_WINDOW_DAYS = 30


class KeywordTrendNotFoundError(NotFoundError):
    code = CODE_KEYWORD_TREND_NOT_FOUND
    message = "关键词不存在"


class KeywordTrendSiteMismatchError(UnprocessableError):
    code = CODE_KEYWORD_TREND_SITE_MISMATCH
    message = "关键词不属于该站点"


class KeywordTrendScopeError(UnprocessableError):
    """站点非法 → 422。与增长榜/词库共用同一个错误码（同属关键词工具的作用域判定）。"""

    code = CODE_KEYWORD_SCOPE_INVALID
    message = "查询作用域不合法"


def _resolve_site(site: str) -> str:
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise KeywordTrendScopeError(str(exc)) from exc


async def _require_keyword_site(db: AsyncSession, keyword_id: int, site: str) -> str:
    """校验 keyword_id 存在且属于 site，返回关键词原文。"""
    row = (await db.execute(crud.keyword_site_stmt(keyword_id))).first()
    if row is None:
        raise KeywordTrendNotFoundError()
    actual_site, keyword_text = row
    if actual_site != site:
        raise KeywordTrendSiteMismatchError(
            f"该关键词属于 {actual_site} 站点，与请求的 {site} 不一致"
        )
    return keyword_text


async def _first_evidence(db: AsyncSession, keyword_id: int) -> KeywordFirstEvidence:
    """三项首次证据，取自关键词维度（不随视图变，详情切视图时同一份）。"""
    row = (await db.execute(crud.keyword_first_evidence_stmt(keyword_id))).one()
    day_d, day_b, week_d, week_b, fs_d, fs_b, fs_p = row
    return KeywordFirstEvidence(
        day=FirstEvidence(date=day_d, basis=day_b),
        week=FirstEvidence(date=week_d, basis=week_b),
        monitor=FirstEvidence(date=fs_d, basis=fs_b, period_type=fs_p),
    )


def _resolve_anchor(available_dates: list[date], on: date | None) -> date | None:
    """解析实际使用的锚定期。

    ``on`` 不要求是该 (site, view) 的一个精确可选日期——它可能来自另一个视图（日/周切换）
    或一个已经过期的来源语境。规则统一是"取 <= on 的最新一期"，取不到就退化为该视图的
    全局最新一期。这既覆盖了同视图的正常情形（on 本身就在集合内，取到的就是它自己），
    也覆盖了跨视图映射（decision 4），不需要为后者单独分支。
    """
    if not available_dates:
        return None
    if on is None:
        return available_dates[0]
    candidates = [d for d in available_dates if d <= on]
    return max(candidates) if candidates else available_dates[0]


def _expected_dates(anchor: date, step_days: int, floor: date) -> list[date]:
    """按视图步长从 anchor 向前枚举期望存在的期次，直到低于 floor（不含）。升序返回。"""
    out: list[date] = []
    cursor = anchor
    step = timedelta(days=step_days)
    while cursor >= floor:
        out.append(cursor)
        cursor -= step
    out.reverse()
    return out


def _floor_for_range(
    range_: str, anchor: date, cutoff: date, available_in_window: list[date]
) -> date:
    if range_ == "RECENT_30D":
        return anchor - timedelta(days=_RECENT_WINDOW_DAYS - 1)
    # ALL：从"该 (site, view) 当前可查询历史的最早期次"开始，且不早于保留下界——
    # 后者是防御性的：即便 site_data_stats 因为尚未跟上保留清理而残留更早的行，
    # 也不把它当成"可用历史"的起点（那些数据物理上已经不在了）。
    in_window = [d for d in available_in_window if d >= cutoff]
    earliest = min(in_window) if in_window else anchor
    # **不能只取 earliest，必须再夹一次 <= anchor。** 锚点本身可能是一个早于保留下界的
    # 残留日期（比如 site_data_stats 里 stale 的一行被选作锚点），此时"在保留窗口内的
    # 最早期次"反而会晚于锚点——不夹的话 `_expected_dates` 从 anchor 走到一个比它更晚
    # 的下界，一步都不会执行，回传空列表，调用方的 `expected[0]` 会直接 IndexError。
    return min(earliest, anchor)


async def get_keyword_trend(
    db: AsyncSession,
    *,
    keyword_id: int,
    site: str,
    view: str,
    range_: str,
    on: date | None,
) -> KeywordTrendResponse:
    resolved_site = _resolve_site(site)
    keyword_text = await _require_keyword_site(db, keyword_id, resolved_site)
    evidence = await _first_evidence(db, keyword_id)

    available_dates = list(
        (await db.execute(analysis_crud.available_dates_stmt(resolved_site, view))).scalars().all()
    )
    anchor = _resolve_anchor(available_dates, on)

    if anchor is None:
        return KeywordTrendResponse(
            keyword_id=keyword_id, keyword=keyword_text, site=resolved_site,
            view=view, range=range_, on=None, first_evidence=evidence,
        )

    cutoff = retention_cutoff(date.today(), settings.HOT_WINDOW_MONTHS)
    floor = _floor_for_range(range_, anchor, cutoff, available_dates)
    step = _STEP_DAYS[view]
    expected = _expected_dates(anchor, step, floor)

    start, end = expected[0], expected[-1]
    history_rows = (
        await db.execute(crud.keyword_history_stmt(resolved_site, view, keyword_id, start, end))
    ).all()
    ranking_by_date: dict[date, int] = {d: r for d, r in history_rows}

    dropped_rows = (
        await db.execute(crud.keyword_dropped_stmt(resolved_site, view, keyword_id, start, end))
    ).all()
    dropped_dates = {d for d, _ in dropped_rows}

    imported_dates = {d for d in available_dates if d >= cutoff}

    points: list[KeywordTrendPoint] = []
    covered: list[date] = []
    for d in expected:
        status: TrendPointStatus
        ranking: int | None = None
        if d < cutoff:
            status = "OUT_OF_RETENTION"
        elif d in ranking_by_date:
            status = "RANKED"
            ranking = ranking_by_date[d]
        elif d in dropped_dates:
            status = "DROPPED"
        elif d in imported_dates:
            status = "NO_DATA"
        else:
            status = "NOT_IMPORTED"
        if status in ("RANKED", "DROPPED", "NO_DATA"):
            covered.append(d)
        points.append(KeywordTrendPoint(date=d, status=status, ranking=ranking))

    coverage = (
        KeywordTrendCoverage(start=min(covered), end=max(covered)) if covered else None
    )

    ranked_points = [p for p in points if p.status == "RANKED"]
    best_ranking = (
        min((KeywordTrendStat(date=p.date, ranking=p.ranking) for p in ranked_points),
            key=lambda s: s.ranking)
        if ranked_points else None
    )
    latest_available = (
        KeywordTrendStat(date=ranked_points[-1].date, ranking=ranked_points[-1].ranking)
        if ranked_points else None
    )

    return KeywordTrendResponse(
        keyword_id=keyword_id, keyword=keyword_text, site=resolved_site,
        view=view, range=range_, on=anchor, coverage=coverage, points=points,
        best_ranking=best_ranking, latest_available=latest_available, first_evidence=evidence,
    )
