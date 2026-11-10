import asyncio
import logging
import os
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api_router import api_router
from app.auth.simple_auth import simple_auth
from app.common import errors
from app.common.error_handlers import json_error, register_error_handlers
from app.common.request_context import RequestIdLogFilter, RequestIdMiddleware
from app.common.request_logging import RequestLoggingMiddleware
from app.jobs.poller import run_poller
from app.table.competitor.store_service import refresh_due_store_round
from app.table.competitor.watch_service import purge_expired_previews_round
from app.table.risk.task_service import reap_risk_tasks_round, run_one_risk_task_round
from app.table.upload.import_queue import (
    drain_one_batch_round,
    reap_stale_uploads_round,
    retry_query_stats_round,
)
from config import settings
from database import async_engine, engine
from monitoring import SystemMonitor


# 配置应用日志，轮转交给宿主机 logrotate（见 etc/keywordiq-logrotate.conf）
def configure_logging():
    import logging.handlers
    os.makedirs("logs", exist_ok=True)

    # 每条日志带 request_id，使用户口述的"刚才报错了"能对回具体一次请求。
    # 不在请求上下文中（后台任务、启动期）时填 "-"。
    #
    # 用 WatchedFileHandler 而非 TimedRotatingFileHandler：Dockerfile 以 4 个独立
    # worker 进程起 uvicorn，每个进程各自 import 本文件时都会调一次
    # configure_logging()，即 4 个独立 handler 实例共同指向同一个物理文件。
    # TimedRotatingFileHandler 的轮转判断与 rename+reopen 都在进程内部独立发生，
    # 没有跨进程协调——Python 官方文档明确说明这不是多进程安全的，4 个进程可能在
    # 午夜前后的同一毫秒级窗口各自尝试 rename，部分进程此后写入指向已改名的旧文件，
    # 表现为"轮转后部分请求日志丢失"。WatchedFileHandler 没有轮转逻辑，只在每次
    # emit() 时比较当前文件的 (dev, ino) 是否变化，变了就重新打开——这是标准库推荐的
    # 多进程安全模式，配合外部按 rename+新建方式做轮转（`create` 模式的 logrotate，
    # 不是 copytruncate——后者对多进程独立写偏移量不安全，见 design.md 决策 A）。
    handlers = [
        logging.handlers.WatchedFileHandler("logs/app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ]
    request_id_filter = RequestIdLogFilter()
    for handler in handlers:
        handler.addFilter(request_id_filter)

    logging.basicConfig(
        level=logging.INFO,
        # %(process)d 辅助多进程定位：一次请求可能落在 4 个 worker 中的任意一个，
        # 出问题时需要知道是哪个进程在写。
        format=(
            "%(asctime)s - %(name)s - [%(request_id)s] - pid=%(process)d - "
            "%(levelname)s - %(message)s"
        ),
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


def configure_thread_limiter() -> int:
    """设置 anyio 默认线程限流器的上限，返回实际生效值。

    走这个限流器的有三类工作，**只有第一类与数据库连接数相关**：同步 def 端点
    （导出）与健康检查探活、bcrypt 哈希/校验、同步 BackgroundTask（导入，一条线程
    占住整场）。所以它用独立的 THREAD_POOL_LIMIT，不从连接池推导——早先写成
    `DB_POOL_SIZE + DB_MAX_OVERFLOW`，一旦按计划把同步池降到 2+3，限流器会跟着
    塌到 5，一个导入在跑就只剩 4 条线程给全部登录。
    """
    limit = settings.THREAD_POOL_LIMIT
    anyio.to_thread.current_default_thread_limiter().total_tokens = limit
    logger.info("🧵 线程池上限: %d（导出/探活/bcrypt/同步后台任务共用）", limit)
    return limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    """异步上下文管理器"""
    # 应用启动时执行
    logger.info(f"🚀 {settings.APP_NAME} v{settings.VERSION} 启动中...")
    logger.info(f"📊 数据库Schema: {settings.DATABASE_SCHEMA}")
    logger.info(f"📁 上传目录: {settings.UPLOAD_DIR}")

    configure_thread_limiter()

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

    # SSO 的默认角色必须真的存在，否则建出来的账号权限是空集——进每个页面都 403，
    # 而管理员看到的是「这人明明登录成功了」。只在**能确定角色不存在**时中断启动；
    # 数据库连不上走上面那个连接检查的记日志路径，不为一个配置校验让应用起不来。
    if settings.SSO_ENABLED:
        from app.auth.sso_service import assert_default_role_exists
        from database import AsyncSessionFactory

        try:
            async with AsyncSessionFactory() as db:
                await assert_default_role_exists(db)
            logger.info("✅ SSO 默认角色检查通过: %s", settings.SSO_DEFAULT_ROLE_CODE)
        except RuntimeError:
            raise
        except Exception:
            logger.exception("⚠️ SSO 默认角色检查未能完成（数据库不可达？），跳过")

    # 排队批次接手：导入串行，抢不到锁的批次留在 QUEUED，靠它在锁释放后接手。
    # 没有它 QUEUED 就是个死标签——界面显示「队列中」，实际没有任何东西会回头看它。
    # job 顺序即优先级，不可调整：先回收超时上传（可能把批次转成排队中，同一轮就能
    # 被紧接着的排队接手抽走）→ 再清理过期的竞对预览行（与排队接手无关，顺带捎带）
    # → 再排空一个导入批次（用户在等的事）→ 队列为空时才拉一个到期独立站（拉取不是
    # 用户在等的事，让路给导入）。
    drainer: asyncio.Task | None = None
    if settings.IMPORT_QUEUE_POLL_SECONDS > 0:
        drainer = asyncio.create_task(
            run_poller(
                [
                    reap_stale_uploads_round,
                    purge_expired_previews_round,
                    drain_one_batch_round,
                    retry_query_stats_round,
                    refresh_due_store_round,
                ],
                settings.IMPORT_QUEUE_POLL_SECONDS,
                name="导入队列",
            )
        )
    else:
        logger.warning("⚠️ IMPORT_QUEUE_POLL_SECONDS=0，排队批次不会自动接手")

    # 外观专利核查任务：独立轮询，不挂在导入队列上——导入一跑几分钟，挂在一起检索会被卡住。
    # 4 个 worker 都跑它，认领靠 SKIP LOCKED 保证同一任务只执行一次。先回收过期租约（转为
    # 结果未知、不重发），再认领一个排队任务。
    risk_runner: asyncio.Task | None = None
    if settings.RISK_TASK_POLL_SECONDS > 0:
        risk_runner = asyncio.create_task(
            run_poller(
                [reap_risk_tasks_round, run_one_risk_task_round],
                settings.RISK_TASK_POLL_SECONDS,
                name="核查任务",
            )
        )
    else:
        logger.warning("⚠️ RISK_TASK_POLL_SECONDS=0，外观专利核查任务不会执行")

    logger.info("🎉 应用启动完成，准备接收请求（版本: %s）", settings.VERSION)

    yield

    # ==================== 关闭事件 ====================
    logger.info("🛑 应用正在关闭...")

    if risk_runner is not None:
        risk_runner.cancel()
        try:
            await risk_runner
        except asyncio.CancelledError:
            logger.info("✅ 核查任务轮询已停止")

    if drainer is not None:
        drainer.cancel()
        try:
            await drainer
        except asyncio.CancelledError:
            logger.info("✅ 导入队列接手任务已停止")

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
# RequestLoggingMiddleware 必须在它之前添加（更内层）——这样执行到这里时 request_id
# 已经建立，摘要日志才能带上正确的值。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)
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
        # 状态码必须是 503 不能是默认的 200：deploy.sh 用 `curl -fsS` 判定就绪，
        # curl -f 只认 HTTP 状态码不解析响应体，此前失败分支返回 200 时 curl 会
        # 把"探测到数据库连不上"误判成"健康检查通过"（PR #84 Codex 第十二轮）——
        # 与本文件错误契约里"503 = 基础设施不可用"是同一条纪律，此前只是没套用到
        # 这个手写端点上。
        #
        # 响应体必须走 error_handlers.json_error，不能手写裸 dict：本仓库的错误契约
        # 是"所有失败响应只有一种形状"（success/code/message/request_id），手写的
        # {"status": "unhealthy"} 不含这四个字段，客户端与排查工具无法把这次失败当成
        # 普通的 503 处理，与 sqlalchemy_exception_handler 处理 OperationalError 时
        # 产出的形状也对不上——两条路径本该是同一类失败（Codex 审核 PR #84 第十九轮）。
        # 复用 errors.CODE_DB_UNAVAILABLE，与那条路径用的是同一个错误码。
        return json_error(503, errors.CODE_DB_UNAVAILABLE, "服务暂时不可用，请稍后重试")


# 进程内长驻，摘要要跨请求才有意义（每请求新建的话"最近 10 次均值"只有一个样本）。
# 历史长度在 SystemMonitor 内部封顶，不会随请求量增长。
system_monitor = SystemMonitor()


@app.get("/api/monitoring/metrics")
async def get_system_metrics(_user=Depends(simple_auth.get_current_user)):
    """获取系统监控指标。

    **要鉴权**：它挂在 `/api` 下由 nginx 反代出去，返回的是宿主机的 CPU / 内存 /
    磁盘 / 进程数。任何权限点都不贴切（这是运维数据不是业务数据），故只要求已登录。

    **采样进线程池**：psutil 要阻塞一整个采样窗口，直接 await 会让该 worker 停摆
    同等时长。这个端点会被轮询，4 个 worker 共享 4 vCPU，停摆是能被用户感知的。
    """
    metrics = await run_in_threadpool(system_monitor.collect_metrics)
    summary = system_monitor.get_performance_summary()

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
