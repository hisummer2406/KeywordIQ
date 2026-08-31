"""Alembic 运行环境。

三处与默认模板不同的配置，都是本项目的硬性要求：

1. **连接串取自 ``config.settings``**，不写在 alembic.ini 里——凭据不进版本库。
2. **``version_table_schema`` 与 ``include_schemas`` 都指向 ``analysis`` schema**。
   本项目的所有对象都在该 schema 下，且连接串不保证 ``search_path`` 指向它，
   因此必须显式限定，不能依赖默认。
3. **``include_object`` 只放行 V2 的对象**。``Base.metadata`` 里同时挂着 V1 的
   模型（V1 退役前仍需运行），若不过滤，autogenerate 会把 V1 的表也一并带进
   V2 的数据库。V1 的表随 V1 废弃，不做迁移。清单与过滤函数在 ``migration_scope.py``
   ——拆出去是为了让测试能 import 到同一份，本模块在 alembic 运行时之外导不进来。
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

# 导入模型模块，让它们注册到 Base.metadata。
from app.table.analysis import dimension_model, fact_model  # noqa: F401
from app.table.competitor import seller_model, store_model, watch_model  # noqa: F401
from app.table.keyword import library_model  # noqa: F401
from app.table.upload import import_batch_model  # noqa: F401
from app.user import user_model  # noqa: F401
from config import settings
from database import Base
from migration_scope import include_object

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata

SCHEMA = settings.DATABASE_SCHEMA


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # 先建 schema，再让 alembic 建它的版本表——版本表配置在 analysis schema 下
        # （version_table_schema），而 schema 本身要等迁移的 upgrade() 才创建，
        # 二者存在先有鸡还是先有蛋的顺序问题。这里提前用幂等 DDL 打破它。
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        connection.commit()

        _configure(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
