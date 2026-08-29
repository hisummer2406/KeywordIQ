"""关键词工具 · 我的词库 API。瘦控制器：调 service，异常交给全局处理器。

- 分组：`GET/POST /library/groups`、`PATCH/DELETE /library/groups/{id}`
- 条目：`POST/DELETE /library/groups/{id}/items`（批量，幂等）
- 盯盘：`POST /library/watch`、`GET /library/groups/{id}/scope`

全部挂 `keyword:view`——词库管理的是用户自己的私有清单，「禁止某人管理自己的收藏夹」
不是会真实发生的授权需求，为它新增权限点只会得到一个全员常开的复选框（`user:manage`
踩过这个坑）。**没有全局鉴权中间件**，漏挂依赖就是裸奔的。

`current_user` 同时是**归属判据**：service 层每一条查询都带它，私有性不靠界面不展示。
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import KEYWORD_VIEW
from app.auth.simple_auth import simple_auth
from app.table.keyword import library_service as service
from app.table.keyword.library_schemas import (
    GroupCreateRequest,
    GroupOut,
    GroupRenameRequest,
    ItemsAddResult,
    ItemsRemoveResult,
    ItemsRequest,
    LibraryScopeInfo,
    WatchRequest,
    WatchResponse,
)
from database import get_async_db

logger = logging.getLogger(__name__)

library_router = APIRouter()

_require_view = simple_auth.require_permission(KEYWORD_VIEW)


@library_router.get("/library/groups", response_model=list[GroupOut])
async def list_groups(
    site: str | None = Query(None, description="按站点过滤，缺省返回全部站点的分组"),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> list[GroupOut]:
    """当前用户的分组列表（带条目数）。"""
    return await service.list_groups(db, user.id, site)


@library_router.post("/library/groups", response_model=GroupOut)
async def create_group(
    req: GroupCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> GroupOut:
    """建组。同站点重名 409，超分组数上限 422。"""
    return await service.create_group(db, user.id, req)


@library_router.patch("/library/groups/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: int,
    req: GroupRenameRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> GroupOut:
    """改名。**不能改站点**——那等于把整组内容换掉。"""
    return await service.rename_group(db, user.id, group_id, req)


@library_router.delete("/library/groups/{group_id}")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> dict:
    """删组，条目连带删除。不属于当前用户的分组一律 404。"""
    await service.delete_group(db, user.id, group_id)
    return {"success": True}


@library_router.post("/library/groups/{group_id}/items", response_model=ItemsAddResult)
async def add_items(
    group_id: int,
    req: ItemsRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> ItemsAddResult:
    """批量加词，幂等。回传新增与已存在的条数——只回「成功」会让用户以为都是新加的。"""
    return await service.add_items(db, user.id, group_id, req.keyword_ids)


@library_router.delete("/library/groups/{group_id}/items", response_model=ItemsRemoveResult)
async def remove_items(
    group_id: int,
    req: ItemsRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> ItemsRemoveResult:
    """批量移除条目。"""
    return await service.remove_items(db, user.id, group_id, req.keyword_ids)


@library_router.get("/library/groups/{group_id}/scope", response_model=LibraryScopeInfo)
async def get_scope(
    group_id: int,
    view: str = Query("DAY", pattern="^(DAY|WEEK)$"),
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> LibraryScopeInfo:
    """该分组所属站点在该视图下的可选期次。站点取自分组，不由调用方指定。"""
    return await service.get_scope(db, user.id, group_id, view)


@library_router.post("/library/watch", response_model=WatchResponse)
async def watch(
    req: WatchRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> WatchResponse:
    """盯盘一页：行集合是分组条目，事实表只补数（掉榜与无数据的词同样占一行）。"""
    return await service.watch(db, user.id, req)
