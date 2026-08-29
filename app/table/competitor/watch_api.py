"""竞对监控 API。瘦控制器：调 service，异常交给全局处理器。

- 分组：`GET/POST /competitor/groups`、`PATCH/DELETE /competitor/groups/{id}`
- 关注：`POST /competitor/watches`（查询，POST 因为请求体含分页/过滤）、
  `PATCH /competitor/watches/{id}`、`DELETE /competitor/watches`（批量）
- 导入两步流：`POST /competitor/watch/preview`（`multipart/form-data`，粘贴文本或文件
  二选一）、`POST /competitor/watch/commit`

全部挂独立权限点 `competitor:view`——不复用 `keyword:view`，每个用户的关注池互相独立。
**没有全局鉴权中间件**，漏挂依赖就是裸奔的。`current_user` 同时是归属判据：service 层
每一条查询都带它，私有性不靠界面不展示。
"""

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import COMPETITOR_VIEW
from app.auth.simple_auth import simple_auth
from app.common.errors import BadRequestError
from app.table.competitor import watch_service as service
from app.table.competitor.watch_schemas import (
    CommitRequest,
    CommitResult,
    GroupCreateRequest,
    GroupOut,
    GroupRenameRequest,
    PreviewResponse,
    WatchListRequest,
    WatchListResponse,
    WatchRemoveRequest,
    WatchUpdateRequest,
)
from database import get_async_db

logger = logging.getLogger(__name__)

competitor_router = APIRouter()

_require_view = simple_auth.require_permission(COMPETITOR_VIEW)

# 书签文件很小（约 2MB），同步读入内存即可——不走 upload 那套 OSS 直传/分块，那是给
# GB 级 CSV 准备的，用在这里是过度设计。这个上限只挡明显异常的上传，不是配额。
_MAX_BOOKMARK_FILE_BYTES = 20 * 1024 * 1024


@competitor_router.get("/groups", response_model=list[GroupOut])
async def list_groups(
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> list[GroupOut]:
    """当前用户的分组列表（带关注数）。"""
    return await service.list_groups(db, user.id)


@competitor_router.post("/groups", response_model=GroupOut)
async def create_group(
    req: GroupCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> GroupOut:
    """建组。同名 409。"""
    return await service.create_group(db, user.id, req)


@competitor_router.patch("/groups/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: int,
    req: GroupRenameRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> GroupOut:
    return await service.rename_group(db, user.id, group_id, req)


@competitor_router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> dict:
    """删组。关注关系不删除，仅解除分组归属（变为未分组）。"""
    await service.delete_group(db, user.id, group_id)
    return {"success": True}


@competitor_router.post("/watches", response_model=WatchListResponse)
async def list_watches(
    req: WatchListRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> WatchListResponse:
    """主表格查询：ProTable 契约 + 配额回显。"""
    return await service.list_watches(db, user.id, req)


@competitor_router.patch("/watches/{watch_id}")
async def update_watch(
    watch_id: int,
    req: WatchUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> dict:
    """改分组 / 改备注（PATCH 语义：缺省字段不改）。"""
    await service.update_watch(db, user.id, watch_id, req)
    return {"success": True}


@competitor_router.delete("/watches")
async def remove_watches(
    req: WatchRemoveRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> dict:
    """批量取消关注。"""
    removed, watch_count = await service.remove_watches(db, user.id, req)
    return {"success": True, "removed": removed, "watch_count": watch_count}


@competitor_router.post("/watch/preview", response_model=PreviewResponse)
async def preview(
    raw_text: str | None = Form(default=None, description="粘贴文本，一行一个链接或裸 ID"),
    fallback_site: str | None = Form(default=None),
    file: UploadFile | None = File(default=None, description="书签 HTML 文件"),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> PreviewResponse:
    """只解析不落库，预览结果存数据库表并设过期时间（替代 Redis）。"""
    bookmark_html: str | None = None
    if file is not None:
        raw_bytes = await file.read()
        if len(raw_bytes) > _MAX_BOOKMARK_FILE_BYTES:
            raise BadRequestError("书签文件过大，请确认上传的是浏览器导出的书签 HTML")
        bookmark_html = raw_bytes.decode("utf-8", errors="replace")

    return await service.preview(
        db, user.id, raw_text=raw_text, bookmark_html=bookmark_html, fallback_site=fallback_site
    )


@competitor_router.post("/watch/commit", response_model=CommitResult)
async def commit(
    req: CommitRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> CommitResult:
    """按预览结果落库。`append`（默认，不覆盖已有分组/备注）或 `replace_group`。"""
    return await service.commit(db, user.id, req)
