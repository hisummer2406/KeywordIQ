"""词库的业务层：归属校验、上限判定、作用域解析、盯盘取数与走势补齐。事务边界在这一层。

三条贯穿全层的约定：

1. **每一次读写都带 ``user_id``。** 私有性是查询条件的产物，不是界面的产物。取不到
   自己的分组一律 404 而非 403——403 会告诉调用方「这个 id 确实存在」。
2. **超上限整批拒绝，不截断。** 截断后的清单与用户以为存进去的不是同一份，而他无从
   察觉。与导出行数上限同一条纪律。
3. **失败一律抛不返。** 路由层不做 try/except 转译，错误体由全局处理器统一产出。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import (
    CODE_KEYWORD_SCOPE_INVALID,
    CODE_LIBRARY_GROUP_DUPLICATE,
    CODE_LIBRARY_GROUP_NOT_FOUND,
    CODE_LIBRARY_KEYWORD_MISMATCH,
    CODE_LIBRARY_LIMIT_EXCEEDED,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
)
from app.site_registry import UnknownSiteError, normalize_site_code
from app.table.analysis import analysis_crud
from app.table.keyword import library_crud as crud
from app.table.keyword.library_schemas import (
    GroupCreateRequest,
    GroupOut,
    GroupRenameRequest,
    ItemsAddResult,
    ItemsRemoveResult,
    LibraryScopeInfo,
    LibraryWatchRow,
    WatchRequest,
    WatchResponse,
)
from config import settings

logger = logging.getLogger(__name__)


class GroupNotFoundError(NotFoundError):
    """分组不存在，或不属于当前用户。

    两种情形共用一个错误：把「存在但不是你的」单独报成 403 等于确认了 id 的存在性，
    而分组 id 是连续自增的，逐个试就能数出别人有多少分组。
    """

    code = CODE_LIBRARY_GROUP_NOT_FOUND
    message = "分组不存在"


class GroupDuplicateError(ConflictError):
    code = CODE_LIBRARY_GROUP_DUPLICATE
    message = "该站点下已有同名分组"


class LibraryLimitError(UnprocessableError):
    code = CODE_LIBRARY_LIMIT_EXCEEDED
    message = "超出词库上限"


class KeywordSiteMismatchError(UnprocessableError):
    code = CODE_LIBRARY_KEYWORD_MISMATCH
    message = "关键词不属于该分组的站点"


_UNIQUE_NAME_CONSTRAINT = "uq_keyword_group_user_site_name"


def _duplicate_name_or_reraise(exc: IntegrityError, detail: str) -> GroupDuplicateError:
    """把重名的完整性错误转成 409，**其余原样抛出**。

    无差别地把 ``IntegrityError`` 当成重名，会让外键失败（比如用户已被删除）也报成
    「该站点下已有同名分组」——一条与事实无关、还会把人引向错误方向的提示。这条路径
    在集成测试里真的踩到过。
    """
    if _UNIQUE_NAME_CONSTRAINT in str(getattr(exc, "orig", exc)):
        return GroupDuplicateError(log_detail=detail)
    raise exc


class LibraryScopeError(UnprocessableError):
    """作用域参数非法（站点未登记、期次无数据/越界）→ 422。

    **不复用分析屏的 `ScopeError`**：错误码按域划分，关键词工具的失败报成 `analysis.*` 会让
    排障时按码筛日志把两条完全不同的路径混在一起（growth 也是这么分的）。
    """

    code = CODE_KEYWORD_SCOPE_INVALID
    message = "查询作用域不合法"


def _resolve_site(site: str) -> str:
    """站点一律校验，非法即报错，**绝不静默回退到默认站点**。"""
    try:
        return normalize_site_code(site)
    except UnknownSiteError as exc:
        raise LibraryScopeError(str(exc)) from exc


# ---------- 分组 ----------

async def list_groups(db: AsyncSession, user_id: int, site: str | None) -> list[GroupOut]:
    resolved = _resolve_site(site) if site else None
    rows = (await db.execute(crud.groups_stmt(user_id, resolved))).all()
    return [GroupOut(**row._mapping) for row in rows]


async def _require_group(db: AsyncSession, user_id: int, group_id: int):
    group = (await db.execute(crud.group_stmt(user_id, group_id))).scalar_one_or_none()
    if group is None:
        raise GroupNotFoundError()
    return group


async def create_group(db: AsyncSession, user_id: int, req: GroupCreateRequest) -> GroupOut:
    site = _resolve_site(req.site)
    name = req.name.strip()
    if not name:
        raise BadRequestError("分组名不能为空")

    existing = (await db.execute(crud.group_count_stmt(user_id, site))).scalar_one()
    cap = settings.LIBRARY_MAX_GROUPS_PER_SITE
    if existing >= cap:
        raise LibraryLimitError(f"该站点下的分组已达上限（{cap} 个），请先删除不用的分组")

    try:
        row = (await db.execute(crud.insert_group_stmt(user_id, site, name))).one()
        await db.commit()
    except IntegrityError as exc:
        # 唯一约束是并发下的最后一道闸：两个请求同时建同名组时，先查后插的"查"都会通过。
        await db.rollback()
        raise _duplicate_name_or_reraise(exc, f"user={user_id} site={site} name={name}") from exc

    return GroupOut(
        group_id=row.group_id, site=site, name=name, item_count=0,
        created_at=row.created_at, updated_at=row.updated_at,
    )


async def rename_group(
    db: AsyncSession, user_id: int, group_id: int, req: GroupRenameRequest
) -> GroupOut:
    await _require_group(db, user_id, group_id)
    name = req.name.strip()
    try:
        await db.execute(crud.rename_group_stmt(user_id, group_id, name))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _duplicate_name_or_reraise(exc, f"user={user_id} group={group_id} name={name}") from exc

    group = await _require_group(db, user_id, group_id)
    count = (await db.execute(crud.item_count_stmt(group_id))).scalar_one()
    return GroupOut(
        group_id=group.group_id, site=group.site, name=group.name, item_count=count,
        created_at=group.created_at, updated_at=group.updated_at,
    )


async def delete_group(db: AsyncSession, user_id: int, group_id: int) -> None:
    """删组。条目由外键 CASCADE 连带删除。"""
    deleted = (await db.execute(crud.delete_group_stmt(user_id, group_id))).scalar_one_or_none()
    if deleted is None:
        await db.rollback()
        raise GroupNotFoundError()
    await db.commit()


# ---------- 条目 ----------

async def add_items(
    db: AsyncSession, user_id: int, group_id: int, keyword_ids: list[int]
) -> ItemsAddResult:
    """批量加词。三道校验都在写之前做完，任一不过则整批不写。"""
    group = await _require_group(db, user_id, group_id)
    unique_ids = list(dict.fromkeys(keyword_ids))  # 保序去重：同一请求里重复给同一个 id 不算两条

    sites = dict((await db.execute(crud.keyword_sites_stmt(unique_ids))).all())
    missing = [kid for kid in unique_ids if kid not in sites]
    if missing:
        raise KeywordSiteMismatchError(
            "有关键词不存在，请刷新查询结果后重试",
            log_detail=f"unknown keyword_ids={missing[:20]}",
        )
    wrong_site = [kid for kid, site in sites.items() if site != group.site]
    if wrong_site:
        raise KeywordSiteMismatchError(
            f"有关键词不属于本分组的站点（{group.site}），请选择该站点下的分组",
            log_detail=f"group={group_id} site={group.site} mismatched={wrong_site[:20]}",
        )

    existing = (await db.execute(crud.item_count_stmt(group_id))).scalar_one()
    cap = settings.LIBRARY_MAX_ITEMS_PER_GROUP
    # 上限按"最坏情况"预判：这批词可能全是新词。宁可少收，也不能先写一半再发现超限——
    # 那正是"静默截断"的另一种形态。
    if existing + len(unique_ids) > cap:
        raise LibraryLimitError(
            f"分组最多 {cap} 个词，当前 {existing} 个，本次要加 {len(unique_ids)} 个，已超出上限"
        )

    inserted = (await db.execute(crud.insert_items_stmt(group_id, unique_ids))).scalars().all()
    await db.commit()
    added = len(inserted)
    return ItemsAddResult(
        added=added, existed=len(unique_ids) - added, item_count=existing + added
    )


async def remove_items(
    db: AsyncSession, user_id: int, group_id: int, keyword_ids: list[int]
) -> ItemsRemoveResult:
    await _require_group(db, user_id, group_id)
    removed = (await db.execute(crud.delete_items_stmt(group_id, keyword_ids))).scalars().all()
    await db.commit()
    count = (await db.execute(crud.item_count_stmt(group_id))).scalar_one()
    return ItemsRemoveResult(removed=len(removed), item_count=count)


# ---------- 盯盘 ----------

async def _available_dates(db: AsyncSession, site: str, view: str) -> list[date]:
    return list((await db.execute(analysis_crud.available_dates_stmt(site, view))).scalars().all())


def _resolve_date(given: date | None, available: list[date]) -> date | None:
    """未传取最新一期；传了必须在可选集内，否则报错不回退。"""
    if given is None:
        return available[0] if available else None
    if given not in available:
        raise LibraryScopeError(f"日期 {given} 无数据或已移出保留窗口")
    return given


async def get_scope(
    db: AsyncSession, user_id: int, group_id: int, view: str
) -> LibraryScopeInfo:
    """盯盘的可选期次。站点取自分组，不由调用方指定。"""
    group = await _require_group(db, user_id, group_id)
    dates = await _available_dates(db, group.site, view)
    return LibraryScopeInfo(site=group.site, view=view, dates=dates)


async def watch(db: AsyncSession, user_id: int, req: WatchRequest) -> WatchResponse:
    """盯盘一页。行集合是分组条目，事实表只补数。"""
    group = await _require_group(db, user_id, req.group_id)
    site, view = group.site, req.view

    dates = await _available_dates(db, site, view)
    on = _resolve_date(req.date, dates)
    total = (await db.execute(crud.item_count_stmt(group.group_id))).scalar_one()

    if on is None or total == 0:
        # 该站点一期数据都没有，或分组还是空的：两种情况都没有行可返回，但**含义不同**，
        # 由界面按 total 与 date 区分（空分组 vs 站点无数据），这里不替它下结论。
        return WatchResponse(
            data=[], total=total, group_id=group.group_id, site=site, view=view, date=on
        )

    offset = (req.page - 1) * req.perPage
    rows = (
        await db.execute(
            crud.watch_stmt(
                group.group_id, site, view, on,
                order_by=req.orderBy, order_dir=req.orderDir,
                offset=offset, limit=req.perPage,
            )
        )
    ).all()
    shaped = crud.shape_watch_rows(rows)

    # 走势与历史排名：先分页再批量补附加，与分析屏同一模式、同一函数。
    # **on/view 必传**——漏传会画出天 8 点 / 周 13 点，且没有任何地方会报错（导出路径踩过一次）。
    ids = [row["keyword_id"] for row in shaped]
    if ids:
        lookback = (
            await db.execute(analysis_crud.trends_stmt(site, ids, on, view))
        ).all()
        trends = analysis_crud.shape_trends(lookback, on=on, view=view)
        trend_dates = analysis_crud.shape_trend_dates(lookback, on=on, view=view)
        history = analysis_crud.shape_history(lookback, on, view)
        for row in shaped:
            kid = row["keyword_id"]
            row["trend"] = trends.get(kid, [])
            row["trend_dates"] = trend_dates.get(kid, [])
            row["history"] = history.get(kid, [None, None, None])

    return WatchResponse(
        data=[LibraryWatchRow(**row) for row in shaped],
        total=total,
        group_id=group.group_id,
        site=site,
        view=view,
        date=on,
    )
