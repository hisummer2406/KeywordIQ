import logging
import time

from sqlalchemy import create_engine, MetaData, event
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

logger = logging.getLogger(__name__)

"""SQLAlchemy 2.0 数据库模型基类"""


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.DATABASE_SCHEMA)


"""数据库连接配置"""
# 同步数据引擎
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

# 异步数据引擎
async_engine = create_async_engine(
    settings.DATABASE_URL_ASYNC,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
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
# 慢查询日志配置 (同步引擎)
# ========================================
SLOW_QUERY_THRESHOLD = 1.0  # 超过 1 秒记录为慢查询


@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """记录查询开始时间"""
    context._query_start_time = time.time()


@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """查询结束后记录慢查询"""
    total = time.time() - context._query_start_time
    if total > SLOW_QUERY_THRESHOLD:
        # 截断过长的 SQL 语句
        sql_preview = statement[:200] + "..." if len(statement) > 200 else statement
        logger.warning(
            f"慢查询检测 ({total:.2f}s): {sql_preview}"
        )


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
