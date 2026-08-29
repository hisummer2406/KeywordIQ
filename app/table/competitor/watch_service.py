"""竞对监控的业务层：归属校验、配额判定、解析编排、预览/确认两步的落库。事务边界在这一层。

三条贯穿全层的约定，与词库（`library_service.py`）一致：

1. **每一次读写都带 ``user_id``。** 私有性是查询条件的产物，不是界面的产物。取不到
   自己的分组/关注/预览一律 404 而非 403——403 会告诉调用方"这个 id 确实存在"。
2. **超配额整批拒绝，不截断。** 配额校验在预览阶段就做，不等到确认阶段才失败。
3. **失败一律抛不返。** 路由层不做 try/except 转译，错误体由全局处理器统一产出。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from app.common.errors import (
    CODE_COMPETITOR_GROUP_DUPLICATE,
    CODE_COMPETITOR_GROUP_NOT_FOUND,
    CODE_COMPETITOR_LIMIT_EXCEEDED,
    CODE_COMPETITOR_PREVIEW_EXPIRED,
    CODE_COMPETITOR_PREVIEW_NOT_FOUND,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableError,
)
from app.table.competitor import watch_crud as crud
from app.table.competitor.seller_ref_parser import (
    derive_group_name,
    extract_bookmark_entries,
    parse_bulk,
    parse_seller_ref,
)
from app.table.competitor.watch_schemas import (
    CommitRequest,
    CommitResult,
    GroupCreateRequest,
    GroupOut,
    GroupRenameRequest,
    PreviewResponse,
    PreviewSample,
    UnparsableEntry,
    WatchListRequest,
    WatchListResponse,
    WatchOut,
    WatchRemoveRequest,
    WatchUpdateRequest,
)
from config import settings
from database import SessionFactory

logger = logging.getLogger(__name__)


class GroupNotFoundError(NotFoundError):
    """分组不存在，或不属于当前用户——两种情形共用一个错误，不单独报 403（id 连续自增）。"""

    code = CODE_COMPETITOR_GROUP_NOT_FOUND
    message = "分组不存在"


class GroupDuplicateError(ConflictError):
    code = CODE_COMPETITOR_GROUP_DUPLICATE
    message = "已有同名分组"


class CompetitorLimitError(UnprocessableError):
    code = CODE_COMPETITOR_LIMIT_EXCEEDED
    message = "超出竞对监控配额"


class PreviewNotFoundError(NotFoundError):
    """token 不存在，或不属于当前用户——同分组一样，不单独报 403。"""

    code = CODE_COMPETITOR_PREVIEW_NOT_FOUND
    message = "预览不存在或已失效，请重新提交"


class PreviewExpiredError(ConflictError):
    code = CODE_COMPETITOR_PREVIEW_EXPIRED
    message = "预览已过期，请重新提交"


_UNIQUE_NAME_CONSTRAINT = "uq_user_seller_group_user_name"


def _duplicate_name_or_reraise(exc: IntegrityError, detail: str) -> GroupDuplicateError:
    if _UNIQUE_NAME_CONSTRAINT in str(getattr(exc, "orig", exc)):
        return GroupDuplicateError(log_detail=detail)
    raise exc


def _status_of(baseline_at) -> str:
    """本变更没有数据获取层，baseline_at 恒为 NULL，因此恒为 PENDING_BASELINE。"""
    return "PENDING_BASELINE" if baseline_at is None else "MONITORING"


# ---------- 分组 ----------


async def list_groups(db: AsyncSession, user_id: int) -> list[GroupOut]:
    rows = (await db.execute(crud.groups_stmt(user_id))).all()
    return [GroupOut(**row._mapping) for row in rows]


async def _require_group(db: AsyncSession, user_id: int, group_id: int):
    group = (await db.execute(crud.group_stmt(user_id, group_id))).scalar_one_or_none()
    if group is None:
        raise GroupNotFoundError()
    return group


async def create_group(db: AsyncSession, user_id: int, req: GroupCreateRequest) -> GroupOut:
    name = req.name.strip()
    if not name:
        raise BadRequestError("分组名不能为空")
    try:
        row = (await db.execute(crud.insert_group_stmt(user_id, name))).one()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _duplicate_name_or_reraise(exc, f"user={user_id} name={name}") from exc
    return GroupOut(
        group_id=row.group_id, name=name, watch_count=0,
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
    count = (await db.execute(crud.watches_in_group_count_stmt(user_id, group_id))).scalar_one()
    return GroupOut(
        group_id=group.group_id, name=group.name, watch_count=count,
        created_at=group.created_at, updated_at=group.updated_at,
    )


async def delete_group(db: AsyncSession, user_id: int, group_id: int) -> None:
    """删组。关注关系由外键 ON DELETE SET NULL 变为未分组，不取消关注。"""
    deleted = (await db.execute(crud.delete_group_stmt(user_id, group_id))).scalar_one_or_none()
    if deleted is None:
        await db.rollback()
        raise GroupNotFoundError()
    await db.commit()


# ---------- 关注关系 ----------


async def list_watches(db: AsyncSession, user_id: int, req: WatchListRequest) -> WatchListResponse:
    if req.group_id is not None:
        await _require_group(db, user_id, req.group_id)

    watch_count = (await db.execute(crud.watch_count_stmt(user_id))).scalar_one()
    total = (
        watch_count
        if req.group_id is None
        else (await db.execute(crud.watches_in_group_count_stmt(user_id, req.group_id))).scalar_one()
    )

    offset = (req.page - 1) * req.perPage
    rows = (
        await db.execute(crud.watches_stmt(user_id, req.group_id, offset, req.perPage))
    ).all()
    data = [
        WatchOut(
            watch_id=r.watch_id, seller_id=r.seller_id, site=r.site,
            group_id=r.group_id, group_name=r.group_name, priority=r.priority,
            note=r.note, status=_status_of(r.baseline_at), created_at=r.created_at,
        )
        for r in rows
    ]
    return WatchListResponse(
        data=data, total=total, watch_count=watch_count, max_watch=settings.COMPETITOR_MAX_WATCH_PER_USER
    )


async def update_watch(
    db: AsyncSession, user_id: int, watch_id: int, req: WatchUpdateRequest
) -> None:
    fields_set = req.model_fields_set
    if "group_id" in fields_set and req.group_id is not None:
        await _require_group(db, user_id, req.group_id)

    updated = (
        await db.execute(
            crud.update_watch_stmt(
                user_id, watch_id,
                group_id=req.group_id, note=req.note,
                set_group="group_id" in fields_set, set_note="note" in fields_set,
            )
        )
    ).scalar_one_or_none()
    if updated is None:
        await db.rollback()
        raise NotFoundError("关注记录不存在")
    await db.commit()


async def remove_watches(
    db: AsyncSession, user_id: int, req: WatchRemoveRequest
) -> tuple[int, int]:
    removed = (await db.execute(crud.delete_watches_stmt(user_id, req.watch_ids))).scalars().all()
    await db.commit()
    watch_count = (await db.execute(crud.watch_count_stmt(user_id))).scalar_one()
    return len(removed), watch_count


# ---------- 预览 / 确认两步 ----------


def _dedup_entries(
    parsed: list, group_names: list[str | None]
) -> list[dict[str, Any]]:
    """按 (seller_id, site) 去重，重复次数计入 priority；分组名取首次出现的非空值。"""
    order: list[tuple[str, str]] = []
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for ref, group_name in zip(parsed, group_names, strict=True):
        key = (ref.seller_id, ref.site)
        if key not in agg:
            order.append(key)
            agg[key] = {
                "seller_id": ref.seller_id, "site": ref.site,
                "priority": 0, "group_name": None,
            }
        agg[key]["priority"] += 1
        if agg[key]["group_name"] is None and group_name:
            agg[key]["group_name"] = group_name
    return [agg[key] for key in order]


async def preview(
    db: AsyncSession,
    user_id: int,
    *,
    raw_text: str | None,
    bookmark_html: str | None,
    fallback_site: str | None,
) -> PreviewResponse:
    if not raw_text and not bookmark_html:
        raise BadRequestError("请提供粘贴文本或书签文件")
    if raw_text and bookmark_html:
        raise BadRequestError("粘贴文本与书签文件二选一，不能同时提供")

    parsed: list = []
    group_names: list[str | None] = []
    if bookmark_html:
        for entry in extract_bookmark_entries(bookmark_html):
            parsed.append(parse_seller_ref(entry.href, fallback_site=fallback_site))
            group_names.append(derive_group_name(entry.folder_path))
    else:
        lines = (raw_text or "").splitlines()
        results = parse_bulk(lines, fallback_site=fallback_site)
        parsed.extend(results)
        group_names.extend([None] * len(results))

    ok_refs = []
    ok_group_names = []
    unparsable: list[UnparsableEntry] = []
    for ref, group_name in zip(parsed, group_names, strict=True):
        if hasattr(ref, "seller_id"):  # ParsedSellerRef
            ok_refs.append(ref)
            ok_group_names.append(group_name)
        else:  # RejectedSellerRef
            unparsable.append(UnparsableEntry(text=ref.text, reason=ref.reason))

    entries = _dedup_entries(ok_refs, ok_group_names)

    cap_batch = settings.COMPETITOR_MAX_IMPORT_BATCH
    if len(entries) > cap_batch:
        raise CompetitorLimitError(
            f"单次可解析的卖家数不能超过 {cap_batch} 个，本次解析出 {len(entries)} 个"
        )

    watched_pairs: set[tuple[str, str]] = set()
    if entries:
        keys = [(e["seller_id"], e["site"]) for e in entries]
        rows = (await db.execute(crud.watched_pairs_stmt(user_id, keys))).all()
        watched_pairs = {(r.seller_id, r.site) for r in rows}

    for e in entries:
        e["is_new"] = (e["seller_id"], e["site"]) not in watched_pairs

    new_count = sum(1 for e in entries if e["is_new"])
    existing_count = len(entries) - new_count

    watch_count = (await db.execute(crud.watch_count_stmt(user_id))).scalar_one()
    cap_total = settings.COMPETITOR_MAX_WATCH_PER_USER
    if watch_count + new_count > cap_total:
        raise CompetitorLimitError(
            f"关注总数上限为 {cap_total} 个，当前已关注 {watch_count} 个，"
            f"本次将新增 {new_count} 个，超出上限"
        )

    preview_token = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.COMPETITOR_PREVIEW_TTL_SECONDS)
    await db.execute(
        crud.insert_preview_stmt(
            preview_token, user_id, {"entries": entries}, expires_at
        )
    )
    await db.commit()

    groups = sorted({e["group_name"] for e in entries if e["group_name"]})
    samples = [
        PreviewSample(
            seller_id=e["seller_id"], site=e["site"], is_new=e["is_new"],
            group_name=e["group_name"],
        )
        for e in entries[:20]
    ]

    return PreviewResponse(
        preview_token=preview_token, expires_at=expires_at,
        parsed=len(entries), new=new_count, existing=existing_count,
        unparsable=unparsable, groups=groups, samples=samples,
    )


async def commit(db: AsyncSession, user_id: int, req: CommitRequest) -> CommitResult:
    row = (await db.execute(crud.preview_stmt(req.preview_token, user_id))).scalar_one_or_none()
    if row is None:
        raise PreviewNotFoundError()
    if row.expires_at < datetime.now(UTC):
        raise PreviewExpiredError()

    entries: list[dict[str, Any]] = row.parsed.get("entries", [])
    if not entries:
        await db.execute(crud.delete_preview_stmt(req.preview_token))
        await db.commit()
        watch_count = (await db.execute(crud.watch_count_stmt(user_id))).scalar_one()
        return CommitResult(
            created_sellers=0, created_watches=0, updated_watches=0,
            removed_watches=0, watch_count=watch_count,
        )

    removed_watches = 0
    if req.mode == "replace_group":
        if req.target_group_id is None:
            raise BadRequestError("replace_group 模式必须指定 target_group_id")
        await _require_group(db, user_id, req.target_group_id)
        deleted = (
            await db.execute(crud.delete_watches_in_group_stmt(user_id, req.target_group_id))
        ).scalars().all()
        removed_watches = len(deleted)
        rows = [
            {
                "user_id": user_id, "seller_id": e["seller_id"], "site": e["site"],
                "group_id": req.target_group_id, "priority": e["priority"], "note": None,
            }
            for e in entries
        ]
        overwrite_group = True
    else:
        distinct_names = sorted({e["group_name"] for e in entries if e["group_name"]})
        name_to_id: dict[str, int] = {}
        if distinct_names:
            existing = (
                await db.execute(crud.existing_group_names_stmt(user_id, distinct_names))
            ).all()
            name_to_id.update({r.name: r.group_id for r in existing})
            missing_names = [n for n in distinct_names if n not in name_to_id]
            if missing_names:
                created = (
                    await db.execute(crud.insert_groups_stmt(user_id, missing_names))
                ).all()
                name_to_id.update({r.name: r.group_id for r in created})
                # 并发建同名组时 ON CONFLICT DO NOTHING 不返回该行，回查一次补全映射。
                still_missing = [n for n in missing_names if n not in name_to_id]
                if still_missing:
                    refetched = (
                        await db.execute(crud.existing_group_names_stmt(user_id, still_missing))
                    ).all()
                    name_to_id.update({r.name: r.group_id for r in refetched})

        if req.target_group_id is not None:
            await _require_group(db, user_id, req.target_group_id)

        rows = [
            {
                "user_id": user_id, "seller_id": e["seller_id"], "site": e["site"],
                "group_id": name_to_id.get(e["group_name"], req.target_group_id)
                if e["group_name"] else req.target_group_id,
                "priority": e["priority"], "note": None,
            }
            for e in entries
        ]
        overwrite_group = False

    seller_rows = [{"seller_id": e["seller_id"], "site": e["site"]} for e in entries]
    created_seller_ids = (
        await db.execute(crud.insert_sellers_stmt(seller_rows))
    ).scalars().all()

    await db.execute(crud.upsert_watches_stmt(rows, overwrite_group=overwrite_group))
    await db.execute(crud.delete_preview_stmt(req.preview_token))
    await db.commit()

    created_watches = sum(1 for e in entries if e["is_new"])
    updated_watches = len(entries) - created_watches
    watch_count = (await db.execute(crud.watch_count_stmt(user_id))).scalar_one()

    return CommitResult(
        created_sellers=len(created_seller_ids),
        created_watches=created_watches,
        updated_watches=updated_watches,
        removed_watches=removed_watches,
        watch_count=watch_count,
    )


def purge_expired_previews(session_factory: sessionmaker = SessionFactory) -> int:
    """清理过期的预览暂存行。挂在 import_queue.py 已有的轮询循环上，不新开定时器。

    自建会话——调用方是同步轮询任务，不复用请求作用域的会话。
    """
    session: Session = session_factory()
    try:
        result = session.execute(crud.delete_expired_previews_stmt(datetime.now(UTC)))
        session.commit()
        return result.rowcount or 0
    finally:
        session.close()
