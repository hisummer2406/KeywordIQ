"""ops-bot 独立 schema 的 SQLAlchemy 声明基类。

**单独成模块，不与 `ops_bot/database.py` 放一起。** 后者在模块级 `create_engine()`，
而 Alembic 迁移与 ORM 模型注册只需要 `OpsBase.metadata`，不需要真的建立数据库连接。
若共用一个模块，`alembic/env.py` 与 `ops_bot/model.py` 的 import 会强制先建 ops-bot
的运行时引擎——而运维手册（`etc/keywordiq-ops-bot-runbook.md`）的安装顺序是先跑
`alembic upgrade head`，再填 `.env` 里「飞书运维机器人」一节，此时 `OPS_DATABASE_URL*`
仍是空字符串默认值，`create_engine("")` 会在 import 阶段直接抛 `ArgumentError`，
把与 ops-bot 毫不相关的业务迁移一并炸掉（PR #99 复审第二轮 P1）。
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

OPS_SCHEMA = "ops"


class OpsBase(DeclarativeBase):
    """ops schema 专用的模型基类，与业务 `database.Base`（`analysis` schema）分开。"""

    metadata = MetaData(schema=OPS_SCHEMA)
