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

``target_metadata`` 是 ``[Base.metadata, OpsBase.metadata]`` 两份列表，不是一份：
飞书运维机器人（ops-bot）的模型独立成一套 ``OpsBase``/``ops`` schema，与业务
``Base``/``analysis`` schema 物理隔离（design.md D1「最小权限的取舍」），但仍要
接进同一条 autogenerate 比对——不接的话，``ops_bot/model.py`` 之后的列改动对
autogenerate 完全不可见（PR #99 Codex 复审 P2）。Alembic 的 autogenerate 原生
支持 ``target_metadata`` 是 MetaData 的列表（内部用 ``util.to_list`` 归一化），
两套 metadata 各自的表名不重叠，不需要合并成一个 MetaData 对象。

``OpsBase`` 从 ``ops_bot.db_base`` 导入，不从 ``ops_bot.database`` 导入：后者在
模块级 ``create_engine()`` 建立 ops-bot 的运行时连接，而按运维手册的安装顺序，
迁移先于 ``.env`` 里「飞书运维机器人」一节的填写执行，此时 ``OPS_DATABASE_URL*``
仍是空字符串默认值——若 env.py 走 ``ops_bot.database`` 这条导入链，会在这一步
直接抛 ``ArgumentError``，把与 ops-bot 无关的业务迁移一并炸掉（PR #99 复审第二轮
P1）。``ops_bot.db_base`` 只有 ``OpsBase``/``OPS_SCHEMA`` 两个声明，不触碰任何
连接配置。
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

# 导入模型模块，让它们注册到 Base.metadata。
from app.table.analysis import dimension_model, fact_model  # noqa: F401
from app.table.competitor import (  # noqa: F401
    asin_model,
    seller_model,
    store_model,
    watch_model,
)
from app.table.keyword import library_model  # noqa: F401
from app.table.pricing import pricing_model  # noqa: F401
from app.table.risk import risk_model  # noqa: F401
from app.table.upload import import_batch_model  # noqa: F401
from app.user import user_model  # noqa: F401
from config import settings
from database import Base
from migration_scope import include_object

# 同理，导入 ops_bot 的模型模块，让它们注册到 OpsBase.metadata。
from ops_bot import model as ops_model  # noqa: F401
from ops_bot.db_base import OpsBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = [Base.metadata, OpsBase.metadata]

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
