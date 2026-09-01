"""独立站监控 API。瘦控制器：调 service，异常交给全局处理器。

- 站点：`POST /competitor/stores`（加入）、`POST /competitor/stores/batch`（批量加入），
  `POST /competitor/stores/list`（清单，
  POST 因为请求体含分页/过滤）、`PATCH /competitor/stores/{domain}`、
  `DELETE /competitor/stores/{domain}`
- 商品：`POST /competitor/store-products`、
  `GET /competitor/stores/{domain}/products/{product_id}/history`
- 上新流：`POST /competitor/new-arrivals`

全部挂 `competitor:view`——**不新增权限点**：「能盯 Amazon 卖家但不能盯独立站」不是一个
真实存在的授权需求，为它开权限点会重蹈 `user:manage` 那种可授予但无区分意义的复选框。

**没有全局鉴权中间件**，漏挂依赖就是裸奔的。`current_user` 同时是归属判据：service 层
每一条查询都带 `user_id`，别人的一律 404 而非 403。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import COMPETITOR_VIEW
from app.auth.simple_auth import simple_auth
from app.table.competitor import store_service as service
from app.table.competitor.store_schemas import (
    NewArrivalRequest,
    NewArrivalResponse,
    ProductHistoryResponse,
    ProductListRequest,
    ProductListResponse,
    StoreAddRequest,
    StoreBatchAddRequest,
    StoreBatchAddResponse,
    StoreListRequest,
    StoreListResponse,
    StoreOut,
    StoreUpdateRequest,
)
from database import get_async_db

logger = logging.getLogger(__name__)

store_router = APIRouter()

_require_view = simple_auth.require_permission(COMPETITOR_VIEW)


@store_router.post("/stores", response_model=StoreOut)
async def add_store(
    req: StoreAddRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> StoreOut:
    """加入并关注一个独立站。

    域名由服务端规范化；**非 Shopify 站点在这一步就被拒绝**，不留到第一次拉取——
    否则它会以「抓取失败」的面目出现，用户会去重试、去等，而不是去换一个站点。
    """
    return await service.add_store(db, user.id, req)


@store_router.post("/stores/batch", response_model=StoreBatchAddResponse)
async def add_stores_batch(
    req: StoreBatchAddRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> StoreBatchAddResponse:
    """一次加入多个独立站，**一步完成**，无预览确认。

    判据是这个操作可不可逆：加错了取关一次点击、已抓到的数据还留着。卖家侧的两步流是
    为 `replace_group` 那种破坏性操作、以及「2657 条来自书签、用户不知道里面有什么」
    设计的，这里两条都不成立。

    响应把每一条输入归入已加入 / 已关注 / 拒绝三段，**拒绝逐条附原因**——只报
    「N 条失败」等于让用户自己去猜是哪几条、为什么。全部被拒时请求仍返回 200：
    那不是错误，是「这一批都不受支持」这个事实。
    """
    return await service.add_stores_batch(db, user.id, req)


@store_router.post("/stores/list", response_model=StoreListResponse)
async def list_stores(
    req: StoreListRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> StoreListResponse:
    """关注中的独立站清单（ProTable 契约）。"""
    return await service.list_stores(db, user.id, req)


@store_router.patch("/stores/{domain}", status_code=204)
async def update_store(
    domain: str,
    req: StoreUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> None:
    """改分组或备注。别人的站点 404 而非 403。"""
    await service.update_store_watch(db, user.id, domain, req)


@store_router.delete("/stores/{domain}", status_code=204)
async def remove_store(
    domain: str,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> None:
    """取消关注。只删关注关系，站点与已抓到的数据都留着（别人可能还在盯）。"""
    await service.remove_store_watch(db, user.id, domain)


@store_router.post("/store-products", response_model=ProductListResponse)
async def list_store_products(
    req: ProductListRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> ProductListResponse:
    """某个独立站的商品清单。响应带 `currency`——价格数字本身不带币种。"""
    return await service.list_products(db, user.id, req)


@store_router.get(
    "/stores/{domain}/products/{product_id}/history", response_model=ProductHistoryResponse
)
async def product_history(
    domain: str,
    product_id: int,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> ProductHistoryResponse:
    """一个商品的调价序列与店内名次走势。

    名次是**店内相对名次，不是销量**，也不可跨店比较——数据源不提供任何绝对销量。
    """
    return await service.product_history(db, user.id, domain, product_id)


@store_router.post("/new-arrivals", response_model=NewArrivalResponse)
async def new_arrivals(
    req: NewArrivalRequest,
    db: AsyncSession = Depends(get_async_db),
    user=Depends(_require_view),
) -> NewArrivalResponse:
    """上新流。

    当前只有独立站一类来源，但响应形状已按两类来源设计：每行带 `source` 与 `time_kind`。
    Amazon 侧接入后是 `DISCOVERED`（发现于），把它标成「上架于」等于拿「我们发现得晚」
    冒充「他上得晚」。
    """
    return await service.new_arrivals(db, user.id, req)
