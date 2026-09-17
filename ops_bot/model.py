"""ops-bot 的任务/审计/去重/告警/备份/基线模型，`ops` schema 下的六张普通表。

**不与业务 `analysis` schema 混用**，也不共用业务 `database.Base`（见 `ops_bot/db_base.py`
的 `OpsBase`）——这六张表的生命周期、写入方与权限边界都只属于 ops-bot 自己。

**`OpsBase` 从 `ops_bot.db_base` 导入，不从 `ops_bot.database` 导入。** 后者会在模块级
建立运行时数据库引擎，而 Alembic 迁移导入本模块时未必已配置好 `OPS_DATABASE_URL*`
（见 `db_base.py` 顶部注释，PR #99 复审第二轮 P1）。

状态枚举与业务 `import_batch` 同一条纪律：普通 `varchar`，取值由应用层保证，增删状态值
不需要一次 `ALTER TYPE`。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ops_bot.db_base import OpsBase

# ---- OpsTask.status ----
TASK_PENDING = "PENDING"
TASK_PENDING_CONFIRM = "PENDING_CONFIRM"
TASK_CONFIRMED = "CONFIRMED"
TASK_RUNNING = "RUNNING"
TASK_SUCCEEDED = "SUCCEEDED"
TASK_FAILED = "FAILED"
TASK_EXPIRED = "EXPIRED"
VALID_TASK_STATUSES = frozenset(
    {TASK_PENDING, TASK_PENDING_CONFIRM, TASK_CONFIRMED, TASK_RUNNING, TASK_SUCCEEDED, TASK_FAILED, TASK_EXPIRED}
)


class OpsTask(OpsBase):
    """一次巡检/告警/备份/重启/发布任务。

    `confirm_token`/`confirm_expires_at` 只在需要人工确认的动作（重启/发布）上使用，
    巡检/告警/备份类任务恒为 NULL——见 design.md D5 的确认状态机。
    """

    __tablename__ = "ops_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="uuid4")

    kind: Mapped[str] = mapped_column(String(32), nullable=False, comment="任务类型，如 RESTART_APP/DEPLOY/BACKUP_NOW")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TASK_PENDING)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="执行输出，截断到 8KB")

    confirm_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by_open_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_ops_task_kind_status", "kind", "status"),
        Index("idx_ops_task_kind_finished", "kind", "finished_at"),
        {"comment": "ops-bot 任务记录：巡检/告警/备份/重启/发布统一落这一张表。"},
    )


class OpsAuditLog(OpsBase):
    """受限动作的审计追加记录。凭证/密钥字段一律不落，落之前先脱敏。"""

    __tablename__ = "ops_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    actor_open_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    target_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False, comment="SUCCEEDED/FAILED")
    failed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_ops_audit_log_task", "task_id"),
        Index("idx_ops_audit_log_created", "created_at"),
        {"comment": "ops-bot 受限动作的审计记录，敏感字段落库前已脱敏。"},
    )


class OpsDedupKey(OpsBase):
    """飞书事件/卡片回调的幂等去重键。"""

    __tablename__ = "ops_dedup_key"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = ({"comment": "飞书事件/回调去重：同一 event_id 只处理一次。"},)


class OpsAlertState(OpsBase):
    """告警去重与维护窗口状态。"""

    __tablename__ = "ops_alert_state"

    alert_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False, default="warn", comment="warn/crit")
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    maintenance_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="发布/重启执行期间的维护窗口截止时间"
    )

    __table_args__ = ({"comment": "巡检告警的去重键、连续计数与维护窗口状态。"},)


class OpsBackupRecord(OpsBase):
    """一次全量备份的产物与结果。"""

    __tablename__ = "ops_backup_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="上传成功后的 OSS object key")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="RUNNING/SUCCEEDED/FAILED")
    failed_stage: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="DUMP/COPY_OUT/UPLOAD")

    __table_args__ = (
        Index("idx_ops_backup_record_status_finished", "status", "finished_at"),
        {"comment": "全量备份记录：任一阶段失败都标注具体失败阶段，不报告为成功。"},
    )


class OpsImportBaseline(OpsBase):
    """导入耗时基线观测值，供人工核对/校准超时阈值——不驱动自动阈值学习。"""

    __tablename__ = "ops_import_baseline"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    period_type: Mapped[str] = mapped_column(String(8), nullable=False, comment="DAY/WEEK")
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="analysis.import_batch.id（无 FK 约束，见迁移说明）")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_ops_import_baseline_period", "period_type", "recorded_at"),
        {"comment": "导入耗时基线观测值：只记录，不驱动自动阈值学习。"},
    )
