"""V2 的导入批次记录。

与 V1 的 ``import_batch_records`` 相比有三处不同：

1. 增加 ``site``——每次导入必须能追溯到具体站点。
2. 用 ``period_type``（``DAY`` / ``WEEK``）取代 V1 的 ``is_day_data`` / ``is_week_data``
   两个布尔位。两个布尔位可以表达出"既不是日报也不是周报"和"既是日报又是周报"
   这两种非法状态，单字段则不能。
3. 增加 ``report_date``——V1 的 ``import_date`` 语义含糊（是报告日期还是导入日期），
   这里明确为报表自身的期次。

表名没有沿用 ``import_batch_records``：那个名字仍被 V1 模型占用，而 V1 在其退役前
仍需正常运行。V1 的表随 V1 一起废弃，不做数据迁移。

状态枚举刻意用 ``varchar`` 而非 PostgreSQL 的 enum 类型——增删状态值不该需要一次
``ALTER TYPE``，这与站点码不用 PG enum 是同一个理由。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.table.analysis.column_comments import (
    PERIOD_COLUMN_COMMENT,
    SITE_COLUMN_COMMENT,
)
from database import Base

# 导入状态。用普通字符串而非 PG enum，取值由应用层保证。
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_QUEUED = "QUEUED"
"""排队中——导入是串行的（2C4G 的 RDS 同时承接两个批量灌入会互相争抢），
拿不到咨询锁的批次进入此状态而非并行开跑。"""

VALID_STATUSES = frozenset({STATUS_QUEUED, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_FAILED})

PERIOD_DAY = "DAY"
PERIOD_WEEK = "WEEK"
VALID_PERIOD_TYPES = frozenset({PERIOD_DAY, PERIOD_WEEK})


class ImportBatch(Base):
    """一次导入的执行记录。"""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    site: Mapped[str] = mapped_column(String(8), nullable=False, comment=SITE_COLUMN_COMMENT)
    period_type: Mapped[str] = mapped_column(String(8), nullable=False, comment=PERIOD_COLUMN_COMMENT)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, comment="报表自身的期次日期")

    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # OSS-first：原始文件直传后的 OSS object key，供溯源与重导；本地文件退路时为空串。
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="原始文件的 OSS object key")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_QUEUED,
        comment="QUEUED（排队）/ PROCESSING / COMPLETED / FAILED",
    )

    total_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="源文件行数")
    written_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="实际写入事实表的行数")
    processing_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 按站点追溯导入历史。
        Index("idx_import_batch_site_period", "site", "period_type", "report_date"),
        Index("idx_import_batch_status", "status"),
        Index("idx_import_batch_created", "created_at"),
        {"comment": "导入批次记录：每次导入可追溯到站点/周期/期次。status 含 QUEUED（导入串行，拿不到咨询锁时排队）。"},
    )
