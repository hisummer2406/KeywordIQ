"""自建 PostgreSQL 运维脚本共用：`--db-name` 默认值跟着 `.env` 的 `POSTGRES_DB` 走。

`POSTGRES_DB` 是 docker-compose.yml 专用的初始化变量，按 `.env.example` 的约定
"不经 config.py"（不进 `Settings`），所以不能读 `settings.POSTGRES_DB`——改用
`python-dotenv` 直接读同一份 `.env`。

`backup_selfhosted_pg.py`、`migrate_to_selfhosted_pg.py`、`verify_selfhosted_migration.py`
三个脚本都要用同一个默认值，抽成这一份共用，不各自复制：这个修法第一次只落在备份脚本里
（Codex 审核 PR #84 第四轮），迁移与核对脚本仍各自硬编码 `"amazon_search"`——操作者一旦
把 `POSTGRES_DB` 改掉，迁移会在 RDS 已被切成只读之后才发现目标库不存在而中断，核对脚本
打印/使用的还原库名同样对不上（Codex 审核 PR #84 第五轮）。三处共用一份，才不会再单独
漏掉第三个。
"""

from __future__ import annotations

FALLBACK_DB_NAME = "amazon_search"


def default_db_name() -> str:
    from dotenv import dotenv_values

    return dotenv_values(".env").get("POSTGRES_DB") or FALLBACK_DB_NAME
