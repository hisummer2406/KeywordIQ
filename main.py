import logging
import os
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api_router import api_router
from app.common.error_handlers import register_error_handlers
from app.common.request_context import RequestIdLogFilter, RequestIdMiddleware
from config import settings
from database import async_engine, engine
from monitoring import SystemMonitor


# 配置应用日志，每天自动生成新文件
def configure_logging():
    import logging.handlers
    os.makedirs("logs", exist_ok=True)

    # 每条日志带 request_id，使用户口述的"刚才报错了"能对回具体一次请求。
    # 不在请求上下文中（后台任务、启动期）时填 "-"。
    handlers = [
        logging.handlers.TimedRotatingFileHandler(
            "logs/app.log", when="midnight", backupCount=7, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ]
    request_id_filter = RequestIdLogFilter()
    for handler in handlers:
        handler.addFilter(request_id_filter)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - [%(request_id)s] - %(levelname)s - %(message)s",
        handlers=handlers,
    )


configure_logging()
logger = logging.getLogger(__name__)


def init_upload_dir():
    try:
        # 确保上传目录存在
        os.makedirs(f"{settings.UPLOAD_DIR}/daily", exist_ok=True)
        os.makedirs(f"{settings.UPLOAD_DIR}/weekly", exist_ok=True)
        logger.info("✅ 上传目录初始化成功")
    except Exception:
        logger.exception("❌ 上传目录初始化失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """异步上下文管理器"""
    # 应用启动时执行
    logger.info(f"🚀 {settings.APP_NAME} v{settings.VERSION} 启动中...")
    logger.info(f"📊 数据库Schema: {settings.DATABASE_SCHEMA}")
    logger.info(f"📁 上传目录: {settings.UPLOAD_DIR}")

    # 线程池上限对齐同步连接池的满载容量。
    #
    # 走线程池的只剩少数同步端点（导出、健康检查探活），它们每个都要占一条同步连接。
    # AnyIO 默认 40 个线程，而同步池满载只有 (DB_POOL_SIZE + DB_MAX_OVERFLOW) 条——
    # 线程数超过可用连接时，多出的线程会卡在连接池上直到 pool_timeout 超时报错。
    # 把并发限制在应用侧：排队快、可观测，且不占数据库连接。
    thread_limit = settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
    anyio.to_thread.current_default_thread_limiter().total_tokens = thread_limit
    logger.info("🧵 线程池上限对齐同步连接池: %d", thread_limit)

    # 初始化上传目录
    init_upload_dir()

    # 检查数据库连接
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            logger.info("✅ 同步数据库连接测试成功")
    except Exception:
        logger.exception("❌ 同步数据库连接测试失败")

    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("✅ 异步数据库连接测试成功")
    except Exception:
        logger.exception("❌ 异步数据库连接测试失败")

    logger.info("🎉 应用启动完成，准备接收请求")

    yield

    # ==================== 关闭事件 ====================
    logger.info("🛑 应用正在关闭...")

    # 关闭数据库连接池
    try:
        engine.dispose()
        logger.info("✅ 同步数据库连接池关闭成功")
    except Exception:
        logger.exception("❌ 同步数据库连接池关闭失败")

    try:
        await async_engine.dispose()
        logger.info("✅ 异步数据库连接池关闭成功")
    except Exception:
        logger.exception("❌ 异步数据库连接池关闭失败")

    logger.info("👋 应用已安全关闭")


# 创建FastAPI应用
#
# ⚠️ 不要传 debug=settings.DEBUG。该模式下 Starlette 会把完整 traceback 写进 500 响应体，
# 生产若 DEBUG=True 即刻泄漏 SQL、文件路径与内部结构。DEBUG 只用来控制 docs 的可见性。
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="亚马逊搜索词分析工具",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan  # 使用新的生命周期管理
)

# 全局异常处理器：所有失败响应的唯一出口（app/common/error_handlers.py）
register_error_handlers(app)

# request id 中间件必须在最外层（后添加的中间件在更外层），这样它建立的上下文对内层的
# CORS、路由与异常处理器都可见，且响应头在任何路径下都能带上。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册 API 路由（后台 UI 已迁至独立 AntD Pro SPA，见 frontend/；本服务只提供 JSON API）
# 鉴权显式落在每个端点的 require_permission / get_current_user 依赖上（不再用全局中间件）。
app.include_router(api_router)


@app.get("/")
async def root():
    return {"service": "KeywordIQ API", "docs": "/docs"}


def _probe_sync_engine() -> None:
    """同步引擎探活。在线程池里跑——它是阻塞调用，不能占事件循环。"""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@app.get("/health")
async def health_check():
    """两个引擎各探一次。健康检查会被轮询，故不能阻塞事件循环。"""
    try:
        await run_in_threadpool(_probe_sync_engine)
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": {
                "sync": "healthy",
                "async": "healthy"
            }
        }
    except Exception:
        # 细节只进日志。此前把 str(e) 写进响应体，而数据库异常的字符串可能带出
        # 连接串与内部路径——健康检查通常是不鉴权的，等于对外泄露。
        logger.exception("❌ 健康检查失败")
        return {"status": "unhealthy"}


# 集成到FastAPI应用
@app.get("/api/monitoring/metrics")
async def get_system_metrics():
    """获取系统监控指标"""
    monitor = SystemMonitor()
    metrics = await monitor.collect_metrics()
    summary = monitor.get_performance_summary()

    return {
        'current_metrics': metrics,
        'summary': summary,
        'status': 'healthy' if metrics['cpu']['total'] < 80 and metrics['memory']['percent'] < 80 else 'warning'
    }


@app.get("/health/pool")
async def pool_status():
    """两个连接池的实时状态 - 用于监控和诊断。

    **两个都要报。** 异步池承载全部面向请求的查询与鉴权，同步池只承载导入、导出与
    迁移；只看其中一个会对全局连接上限得出错误结论——历史上文档里的算式正是因为
    漏算了一个引擎而系统性低估了一半。
    """
    sync_pool = engine.pool
    async_pool = async_engine.pool
    per_worker_ceiling = (
        settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW
        + settings.DB_ASYNC_POOL_SIZE + settings.DB_ASYNC_MAX_OVERFLOW
    )

    return {
        "sync_engine": {
            "role": "导入 COPY / 导出游标 / 迁移",
            "pool_size": sync_pool.size(),
            "checked_in": sync_pool.checkedin(),
            "checked_out": sync_pool.checkedout(),
            "overflow": sync_pool.overflow(),
            "max_overflow": settings.DB_MAX_OVERFLOW,
        },
        "async_engine": {
            "role": "面向请求的查询与鉴权",
            "pool_size": async_pool.size(),
            "checked_in": async_pool.checkedin(),
            "checked_out": async_pool.checkedout(),
            "overflow": async_pool.overflow(),
            "max_overflow": settings.DB_ASYNC_MAX_OVERFLOW,
        },
        "config": {
            "sync": {
                "pool_size": settings.DB_POOL_SIZE,
                "max_overflow": settings.DB_MAX_OVERFLOW,
            },
            "async": {
                "pool_size": settings.DB_ASYNC_POOL_SIZE,
                "max_overflow": settings.DB_ASYNC_MAX_OVERFLOW,
            },
            "pool_timeout": settings.DB_POOL_TIMEOUT,
            "pool_recycle": settings.DB_POOL_RECYCLE,
            # 单 worker 满载上限；乘 worker 数即全局上限，须远低于 RDS 的 max_connections
            "per_worker_ceiling": per_worker_ceiling,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=2,  # 使用2个worker
        timeout_keep_alive=600,
        reload=settings.DEBUG,
    )
