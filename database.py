import logging
import time

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

logger = logging.getLogger(__name__)

"""SQLAlchemy 2.0 数据库模型基类"""


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.DATABASE_SCHEMA)


"""数据库连接配置"""
# 同步数据引擎：服务导入的 COPY（psycopg2 原始游标，asyncpg 无等价接口）、导出的
# 服务端游标、Alembic 迁移。都是低并发长事务，池可以开得小。
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,  # 自动检测并重置失效连接
    echo=settings.DEBUG,
    connect_args={
        "options": "-c timezone=Asia/Shanghai",
        # TCP keepalive：让内核在约 60 秒内识破被静默丢弃的连接（默认要等 2 小时）。
        # 若不开，云上负载均衡/NAT 空闲超时或 RDS 过载时连接会被无声丢弃（不发 FIN），
        # 内核仍认为 socket 存活，pool_pre_ping 的 SELECT 1 写进黑洞永久阻塞——连接池
        # 被挂死的 checkout 占光，整个 worker 卡死到重启。这正是导入期偶发卡死的疑似机制。
        # keepalives_idle*count + interval ≈ 30 + 3×10 = 60 秒后判定连接已死。
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        # 建连本身也加超时，避免建连阶段无限等待。
        "connect_timeout": 10,
    }
)

# 会话工厂
SessionFactory = sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

# 异步数据引擎：服务面向请求的查询与鉴权路径——随用户数增长的就是这一条，
# 故池配置与同步引擎分开（DB_ASYNC_*），不再共用一组取值。
async_engine = create_async_engine(
    settings.DATABASE_URL_ASYNC,
    pool_size=settings.DB_ASYNC_POOL_SIZE,
    max_overflow=settings.DB_ASYNC_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,  # 自动检测并重置失效连接
    echo=settings.DEBUG,
    connect_args={
        # asyncpg 不接受 psycopg2 的 keepalives 系列参数（那是 socket 层选项，
        # 不是 PG 的 server_settings）。异步引擎只服务 API 读查询，不走导入路径，
        # 而已知的导入期卡死发生在同步引擎——TCP keepalive 已在同步引擎补上。
        # 若将来异步侧也出现半开连接问题，需用 asyncpg 的 socket 层配置单独处理。
        "server_settings": {
            "timezone": "Asia/Shanghai"
        }
    }
)

# 异步会话工厂
AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,  # 异步会话默认不自动 flush
    autocommit=False,
)


# ========================================
# 慢查询日志（同步 + 异步引擎均覆盖）
# ========================================
# 此前这两个事件监听只挂在同步引擎（engine）上——服务导入/导出/迁移；而
# /api/analysis/search 等面向请求的查询全部走异步引擎（async_engine，asyncpg），
# 对这条最高频的读路径完全没有慢查询记录。cursor 级事件是 DBAPI 层面的，
# AsyncEngine 没有自己的这套事件，要挂在它内部持有的同步 Engine 上
# （``async_engine.sync_engine``）才会在异步查询时同样触发。
# 阈值改为可配置（settings.SLOW_QUERY_THRESHOLD_SECONDS），不再硬编码。


def _register_slow_query_listener(target_engine) -> None:
    @event.listens_for(target_engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = time.time()

    @event.listens_for(target_engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        total = time.time() - context._query_start_time
        if total > settings.SLOW_QUERY_THRESHOLD_SECONDS:
            # 截断过长的 SQL 语句；只记语句文本不记参数值，参数可能带筛选/检索词。
            sql_preview = statement[:200] + "..." if len(statement) > 200 else statement
            logger.warning(f"慢查询检测 ({total:.2f}s): {sql_preview}")

    @event.listens_for(target_engine, "handle_error")
    def handle_error(exception_context):
        """执行失败（语句超时、锁等待超时、连接池 checkout 超时等）不会走到
        ``after_cursor_execute``——DBAPI 根本没有成功返回，慢查询检测因此对"执行
        失败的慢查询"完全失明，而这恰恰是最值得诊断的一类（连接卡死、锁等待，见
        issue #4）（Codex 复审 PR #98 P2）。

        ``execution_context`` 在游标执行之外发生的失败（建连、``pool_pre_ping``
        探测）里可能是 ``None``，或没有 ``before_cursor_execute`` 设过的起始
        时间，两种都跳过不报——没有起始时间就无法判断"慢"，报了也是编造。
        不返回任何值：不修改、不吞掉异常，原始异常照常向上传播。
        """
        ctx = exception_context.execution_context
        start = getattr(ctx, "_query_start_time", None) if ctx is not None else None
        if start is None:
            return
        total = time.time() - start
        if total > settings.SLOW_QUERY_THRESHOLD_SECONDS:
            statement = exception_context.statement or ""
            sql_preview = statement[:200] + "..." if len(statement) > 200 else statement
            logger.warning(f"慢查询检测（执行失败） ({total:.2f}s): {sql_preview}")


_register_slow_query_listener(engine)
_register_slow_query_listener(async_engine.sync_engine)


# 获取同步数据库会话 (FastAPI 依赖注入)
def get_db():
    """同步数据库会话生成器 - 用于 FastAPI Depends"""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


# 异步数据库会话 (FastAPI 依赖注入)
async def get_async_db():
    """异步数据库会话生成器 - 用于 FastAPI Depends"""
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            # async with 会自动关闭，这里不需要显式 close
            pass
