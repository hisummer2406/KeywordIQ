"""飞书运维机器人：ops schema、ops_bot_role 角色、六张状态表

Revision ID: d4e5f6a7b8c9
Revises: 71e646b2d200
Create Date: 2026-09-16

issue #90 / openspec/changes/add-feishu-ops-bot 的数据库底座。ops-bot 是与业务
app 完全独立的进程/容器，这条迁移不改动 `analysis` schema 下任何既有对象，只新增：

1. `ops` schema——ops-bot 的任务/审计/去重/告警/备份/基线状态表都落在这里，
   与业务星型模型物理隔离。
2. `ops_bot_role` 数据库角色——ops-bot 用它连接，不与业务应用共用凭证
   （design.md D1「最小权限的取舍」）。权限边界：对 `ops` schema 完整权限，对
   `analysis.import_batch` / `analysis.site_data_stats` 仅 SELECT，对其余业务表
   没有任何权限。密码由环境变量 `OPS_DB_ROLE_PASSWORD` 驱动（直接读 `os.environ`，
   不经 `config.Settings`——它只有这一条迁移一个读取方，与 `PG_MAX_CONNECTIONS`/
   `POSTGRES_DB` 同一条既有约定），每次 upgrade 都会 (re)set——这条迁移因此不是纯
   幂等 DDL，重复执行会把角色密码同步成配置当下的值，这是刻意的（切换密码不必单独
   写一条迁移）。
3. 六张普通表（`ops_task`/`ops_audit_log`/`ops_dedup_key`/`ops_alert_state`/
   `ops_backup_record`/`ops_import_baseline`），字段与 `ops_bot/model.py` 一一对应。

**不登记进 `migration_scope.V2_TABLES` / `alembic/env.py` 的 `target_metadata`**：
`ops_bot/model.py` 用独立的 `OpsBase`（`ops_bot/database.py`），不挂在业务
`database.Base` 下，autogenerate 本来就看不见它——这条迁移的 `upgrade()`/`downgrade()`
是手写的 DDL，不依赖 autogenerate 比对，因此这个"看不见"不会制造出「没有差异」的假绿
（那是 V2_TABLES 白名单要防的问题，针对的是共用 `database.Base` 却忘记登记的表）。

`ops_import_baseline.batch_id` 不建外键指向 `analysis.import_batch.id`：
`ops_bot_role` 对该表只有 SELECT 权限，没有 REFERENCES 权限，建 FK 约束需要引用方
角色具备 REFERENCES 权限，为了一个观测性质的弱引用去扩大授权面不划算——
应用层保证写入的 batch_id 来自一次真实读取。
"""
import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from config import settings

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "71e646b2d200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPS_SCHEMA = "ops"
ANALYSIS_SCHEMA = settings.DATABASE_SCHEMA
OPS_ROLE = "ops_bot_role"


def _pg_string_literal(value: str) -> str:
    """转成 PostgreSQL 字符串字面量，供直接拼进 DDL/DCL 语句。

    只用于本迁移内的角色密码，值来自受信任的部署配置（`OPS_DB_ROLE_PASSWORD`），
    不是外部用户输入。**特意不用 SQLAlchemy 的 bindparams**：`ALTER ROLE ... PASSWORD`
    这个位置在 PostgreSQL 语法里是字符串字面量（`Sconst`），不接受参数化占位符。

    `op.execute()` 对纯字符串一律先包一层 `sa.text()`，而 `sa.text()` 会扫描 `:name`
    形式当作绑定参数——密码里若恰好出现"冒号+标识符"会被误判成"缺少绑定值"而报错。
    这个约束本来就自然成立：同一个密码还要出现在 `OPS_DATABASE_URL` 连接串里
    （`user:password@host` 形式），本就不能含冒号，这里用断言把这条隐性约束显式化，
    而不是留给一次生产部署踩中才发现。
    """
    if ":" in value:
        raise ValueError(
            "OPS_DB_ROLE_PASSWORD 不能包含冒号——它同时出现在 OPS_DATABASE_URL 连接串"
            "（user:password@host 形式）里，含冒号会破坏连接串解析，也会让本迁移的"
            "占位符扫描误判。"
        )
    return "'" + value.replace("'", "''") + "'"


def _ops_db_role_password() -> str:
    """读 `OPS_DB_ROLE_PASSWORD`：先查 `os.environ`，查不到再回退读 `.env` 文件。

    两步都不能省，同 `config._validate_pool_within_self_hosted_pg_cap` 对
    `PG_MAX_CONNECTIONS` 的处理——迁移在 `app` 容器内跑时（生产常态）它由
    compose 的 `env_file: .env` 注入成了真正的环境变量；本地直接 `uv run alembic
    upgrade head` 时进程环境里通常没有它，但 `.env` 文件就在当前工作目录。
    """
    value = os.environ.get("OPS_DB_ROLE_PASSWORD")
    if value is not None:
        return value
    from dotenv import dotenv_values

    return dotenv_values(".env").get("OPS_DB_ROLE_PASSWORD") or "change-me-in-production"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{OPS_SCHEMA}"')

    # 角色不存在则建、存在则只同步密码——两种情形都用同一段 DO 块处理，
    # 幂等且不依赖"先查后建"的应用层判断。
    password_literal = _pg_string_literal(_ops_db_role_password())
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{OPS_ROLE}') THEN
                CREATE ROLE {OPS_ROLE} LOGIN PASSWORD {password_literal};
            ELSE
                ALTER ROLE {OPS_ROLE} WITH PASSWORD {password_literal};
            END IF;
        END
        $$;
        """
    )

    op.execute(f'GRANT USAGE, CREATE ON SCHEMA "{OPS_SCHEMA}" TO {OPS_ROLE}')
    # 对 analysis 下两张巡检要读的表只给只读权限，不给其余任何业务表任何权限。
    op.execute(f'GRANT USAGE ON SCHEMA "{ANALYSIS_SCHEMA}" TO {OPS_ROLE}')
    op.execute(f'GRANT SELECT ON "{ANALYSIS_SCHEMA}".import_batch TO {OPS_ROLE}')
    op.execute(f'GRANT SELECT ON "{ANALYSIS_SCHEMA}".site_data_stats TO {OPS_ROLE}')

    # ---- 六张表 ----
    op.create_table(
        "ops_task",
        sa.Column("id", sa.String(length=36), nullable=False, comment="uuid4"),
        sa.Column("kind", sa.String(length=32), nullable=False,
                  comment="任务类型，如 RESTART_APP/DEPLOY/BACKUP_NOW"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", sa.Text(), nullable=False, server_default="",
                  comment="执行输出，截断到 8KB"),
        sa.Column("confirm_token", sa.String(length=36), nullable=True),
        sa.Column("confirm_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_open_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=OPS_SCHEMA,
        comment="ops-bot 任务记录：巡检/告警/备份/重启/发布统一落这一张表。",
    )
    op.create_index("idx_ops_task_kind_status", "ops_task", ["kind", "status"], schema=OPS_SCHEMA)
    op.create_index("idx_ops_task_kind_finished", "ops_task", ["kind", "finished_at"], schema=OPS_SCHEMA)

    op.create_table(
        "ops_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("actor_open_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("target_sha", sa.String(length=40), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False, comment="SUCCEEDED/FAILED"),
        sa.Column("failed_stage", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=OPS_SCHEMA,
        comment="ops-bot 受限动作的审计记录，敏感字段落库前已脱敏。",
    )
    op.create_index("idx_ops_audit_log_task", "ops_audit_log", ["task_id"], schema=OPS_SCHEMA)
    op.create_index("idx_ops_audit_log_created", "ops_audit_log", ["created_at"], schema=OPS_SCHEMA)

    op.create_table(
        "ops_dedup_key",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        schema=OPS_SCHEMA,
        comment="飞书事件/回调去重：同一 event_id 只处理一次。",
    )

    op.create_table(
        "ops_alert_state",
        sa.Column("alert_key", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False, server_default="warn"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_until", sa.DateTime(timezone=True), nullable=True,
                  comment="发布/重启执行期间的维护窗口截止时间"),
        sa.PrimaryKeyConstraint("alert_key"),
        schema=OPS_SCHEMA,
        comment="巡检告警的去重键、连续计数与维护窗口状态。",
    )

    op.create_table(
        "ops_backup_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=True,
                  comment="上传成功后的 OSS object key"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, comment="RUNNING/SUCCEEDED/FAILED"),
        sa.Column("failed_stage", sa.String(length=16), nullable=True, comment="DUMP/COPY_OUT/UPLOAD"),
        sa.PrimaryKeyConstraint("id"),
        schema=OPS_SCHEMA,
        comment="全量备份记录：任一阶段失败都标注具体失败阶段，不报告为成功。",
    )
    op.create_index("idx_ops_backup_record_status_finished", "ops_backup_record",
                     ["status", "finished_at"], schema=OPS_SCHEMA)

    op.create_table(
        "ops_import_baseline",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("period_type", sa.String(length=8), nullable=False, comment="DAY/WEEK"),
        sa.Column("batch_id", sa.BigInteger(), nullable=False,
                  comment="analysis.import_batch.id（无 FK 约束，见迁移说明）"),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=OPS_SCHEMA,
        comment="导入耗时基线观测值：只记录，不驱动自动阈值学习。",
    )
    op.create_index("idx_ops_import_baseline_period", "ops_import_baseline",
                     ["period_type", "recorded_at"], schema=OPS_SCHEMA)

    # 六张表都由本迁移的执行角色（app 的 DATABASE_URL 用户）创建、持有，
    # 显式 GRANT 给 ops_bot_role 完整权限；ALTER DEFAULT PRIVILEGES 覆盖未来
    # 由同一执行角色在 ops schema 下新建的表/序列，避免下次新增表时忘记补授权。
    op.execute(f'GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{OPS_SCHEMA}" TO {OPS_ROLE}')
    op.execute(f'GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{OPS_SCHEMA}" TO {OPS_ROLE}')
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{OPS_SCHEMA}" GRANT ALL PRIVILEGES ON TABLES TO {OPS_ROLE}'
    )
    op.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{OPS_SCHEMA}" GRANT ALL PRIVILEGES ON SEQUENCES TO {OPS_ROLE}'
    )


def downgrade() -> None:
    """回滚不影响 analysis schema 下任何数据；ops schema 整体随六张表一起丢弃。"""
    op.execute(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{OPS_SCHEMA}" FROM {OPS_ROLE}')
    op.drop_index("idx_ops_import_baseline_period", table_name="ops_import_baseline", schema=OPS_SCHEMA)
    op.drop_table("ops_import_baseline", schema=OPS_SCHEMA)
    op.drop_index("idx_ops_backup_record_status_finished", table_name="ops_backup_record", schema=OPS_SCHEMA)
    op.drop_table("ops_backup_record", schema=OPS_SCHEMA)
    op.drop_table("ops_alert_state", schema=OPS_SCHEMA)
    op.drop_table("ops_dedup_key", schema=OPS_SCHEMA)
    op.drop_index("idx_ops_audit_log_created", table_name="ops_audit_log", schema=OPS_SCHEMA)
    op.drop_index("idx_ops_audit_log_task", table_name="ops_audit_log", schema=OPS_SCHEMA)
    op.drop_table("ops_audit_log", schema=OPS_SCHEMA)
    op.drop_index("idx_ops_task_kind_finished", table_name="ops_task", schema=OPS_SCHEMA)
    op.drop_index("idx_ops_task_kind_status", table_name="ops_task", schema=OPS_SCHEMA)
    op.drop_table("ops_task", schema=OPS_SCHEMA)

    op.execute(f'REVOKE SELECT ON "{ANALYSIS_SCHEMA}".site_data_stats FROM {OPS_ROLE}')
    op.execute(f'REVOKE SELECT ON "{ANALYSIS_SCHEMA}".import_batch FROM {OPS_ROLE}')
    op.execute(f'REVOKE USAGE ON SCHEMA "{ANALYSIS_SCHEMA}" FROM {OPS_ROLE}')
    op.execute(f'REVOKE USAGE, CREATE ON SCHEMA "{OPS_SCHEMA}" FROM {OPS_ROLE}')
    op.execute(f'DROP SCHEMA IF EXISTS "{OPS_SCHEMA}" CASCADE')
    op.execute(f"DROP ROLE IF EXISTS {OPS_ROLE}")
