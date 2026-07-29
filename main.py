import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api_router import api_router
from config import settings
from database import engine, async_engine
from monitoring import SystemMonitor
from app.auth.auth_middleware import AdminAuthMiddleware


# 配置应用日志，每天自动生成新文件
def configure_logging():
    import logging.handlers
    os.makedirs("logs", exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.handlers.TimedRotatingFileHandler(
                "logs/app.log", when="midnight", backupCount=7, encoding="utf-8"
            ),
            logging.StreamHandler(),
        ],
    )
    # 控制台输出
    logging.StreamHandler()


configure_logging()
logger = logging.getLogger(__name__)


def init_upload_dir():
    try:
        # 确保上传目录存在
        os.makedirs(f"{settings.UPLOAD_DIR}/daily", exist_ok=True)
        os.makedirs(f"{settings.UPLOAD_DIR}/weekly", exist_ok=True)
        logger.info("✅ 上传目录初始化成功")
    except Exception as e:
        logger.error(f"❌ 上传目录初始化失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """异步上下文管理器"""
    # 应用启动时执行
    logger.info(f"🚀 {settings.APP_NAME} v{settings.VERSION} 启动中...")
    logger.info(f"📊 数据库Schema: {settings.DATABASE_SCHEMA}")
    logger.info(f"📁 上传目录: {settings.UPLOAD_DIR}")
    logger.info(f"📊 进程数量: {settings.MAX_WORKERS}")
    logger.info(f"⚙️ 批处理大小: {settings.BATCH_SIZE}")
    logger.info(f"📁 文件分块行数: {settings.FILE_SPLIT_LINES}")

    # 初始化上传目录
    init_upload_dir()

    # 检查数据库连接
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            logger.info("✅ 同步数据库连接测试成功")
    except Exception as e:
        logger.error(f"❌ 同步数据库连接测试失败: {e}")

    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("✅ 异步数据库连接测试成功")
    except Exception as e:
        logger.error(f"❌ 异步数据库连接测试失败: {e}")

    logger.info("🎉 应用启动完成，准备接收请求")

    yield

    # ==================== 关闭事件 ====================
    logger.info("🛑 应用正在关闭...")

    # 关闭数据库连接池
    try:
        engine.dispose()
        logger.info("✅ 同步数据库连接池关闭成功")
    except Exception as e:
        logger.error(f"❌ 同步数据库连接池关闭失败: {e}")

    try:
        await async_engine.dispose()
        logger.info("✅ 异步数据库连接池关闭成功")
    except Exception as e:
        logger.error(f"❌ 异步数据库连接池关闭失败: {e}")

    logger.info("👋 应用已安全关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="亚马逊搜索词分析工具",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan  # 使用新的生命周期管理
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 登录验证（JWT 鉴权中间件，保护 /api/* 受保护路径）
app.add_middleware(AdminAuthMiddleware)

# 注册 API 路由（后台 UI 已迁至独立 AntD Pro SPA，见 frontend/；本服务只提供 JSON API）
app.include_router(api_router)


@app.get("/")
async def root():
    return {"service": "KeywordIQ API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        async with async_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": {
                "sync": "healthy",
                "async": "healthy"
            }
        }
    except Exception as e:
        logger.error(f"❌ 健康检查失败: {e}")
        return {"status": "unhealthy", "error": str(e)}


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
    """获取数据库连接池状态 - 用于监控和诊断"""
    from sqlalchemy import inspect

    sync_inspect = inspect(engine)

    return {
        "sync_engine": {
            "pool_size": sync_inspect.pool.size(),
            "checked_in": sync_inspect.pool.checkedin(),
            "checked_out": sync_inspect.pool.checkedout(),
            "overflow": sync_inspect.pool.overflow(),
            "max_overflow": settings.DB_MAX_OVERFLOW,
        },
        "config": {
            "pool_size": settings.DB_POOL_SIZE,
            "max_overflow": settings.DB_MAX_OVERFLOW,
            "pool_timeout": settings.DB_POOL_TIMEOUT,
            "pool_recycle": settings.DB_POOL_RECYCLE,
        }
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
