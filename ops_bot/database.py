"""ops-bot 独立数据库引擎。

**不复用业务 `database.py` 的 `engine`/`async_engine`/`Base`。** ops-bot 用
`OPS_DATABASE_URL*`（`ops_bot_role` 凭证，见迁移里的角色授权）连接同一个自建
PostgreSQL 容器，但物理上是不同的数据库角色——复用业务引擎会让 ops-bot 的每一次
查询都带着业务主账号的权限，架空了「最小权限」这条设计决策
（design.md D1「最小权限的取舍」）。

池配置刻意不进 `config.Settings`：ops-bot 的负载是巡检轮询 + 少量飞书回调，
量级与业务查询路径完全不是一回事，硬做成配置项只会制造第二套需要维护的旋钮。

**`OpsBase` 定义在 `ops_bot/db_base.py`，本模块只 re-export。** 本模块在模块级
`create_engine()`——Alembic 与模型注册只需要 metadata，不该被迫先建立运行时连接，
理由见 `db_base.py` 顶部注释。运行时代码仍可以照旧 `from ops_bot.database import
OpsBase`，只是它自己不再是定义处。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import settings
from ops_bot.db_base import OPS_SCHEMA, OpsBase  # noqa: F401

ops_engine = create_engine(
    settings.OPS_DATABASE_URL,
    pool_size=2,
    max_overflow=2,
    pool_pre_ping=True,
    pool_recycle=1800,
)

OpsSessionFactory = sessionmaker(
    bind=ops_engine,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

ops_async_engine = create_async_engine(
    settings.OPS_DATABASE_URL_ASYNC,
    pool_size=4,
    max_overflow=4,
    pool_pre_ping=True,
    pool_recycle=1800,
)

OpsAsyncSessionFactory = async_sessionmaker(
    bind=ops_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)
