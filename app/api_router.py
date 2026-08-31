from fastapi import APIRouter

from app.auth.auth_api import auth_router
from app.table.analysis.analysis_api import analysis_router
from app.table.competitor.store_api import store_router
from app.table.competitor.watch_api import competitor_router
from app.table.keyword.growth_api import growth_router
from app.table.keyword.library_api import library_router
from app.table.upload.upload_api import upload_router
from app.user.user_api import user_router

api_router = APIRouter(prefix="/api" , tags=["API"])

# 认证路由
api_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["认证"]
)

api_router.include_router(
    analysis_router,
    prefix="/analysis",
    tags=["数据分析"]
)

api_router.include_router(
    growth_router,
    prefix="/keyword",
    tags=["关键词工具"]
)

# 词库与增长榜同属「关键词工具」菜单、同一个权限点，分成两个 router 只是为了让模块边界
# 与文件对应。
api_router.include_router(
    library_router,
    prefix="/keyword",
    tags=["关键词工具"]
)

api_router.include_router(
    upload_router,
    prefix="/upload",
    tags=["数据上传"]
)

api_router.include_router(
    competitor_router,
    prefix="/competitor",
    tags=["竞对监控"]
)

# 独立站监控与卖家关注同挂 /competitor 前缀、同一个权限点——它们是同一件事的两类对象，
# 不是两个功能域。
api_router.include_router(
    store_router,
    prefix="/competitor",
    tags=["竞对监控"]
)

api_router.include_router(
    user_router,
    prefix="/user",
    tags=["用户中心"]
)
