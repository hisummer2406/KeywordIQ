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

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, SmallInteger, String, Text, func
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
STATUS_UPLOADING = "UPLOADING"
"""直传中——签发直传凭证时即落此状态，早于导入被触发。

它存在的理由是直传那一段服务端本来什么都不知道：GB 级字节不经应用服务器，用户关掉
标签页、断网、或分片上传最后一步失败，此前库里连一条记录都不会有，用户以为传上去了、
历史屏也查不到。有了这条记录，「传到一半没了」至少是可见的。

**与 QUEUED 同一条纪律：它必须会被收尾。** 没人接手的状态就是个死标签——界面永远显示
「上传中」，比什么都不显示只是换了句谎话。收尾见 ``import_service.reap_stale_uploads``。"""

VALID_STATUSES = frozenset({
    STATUS_UPLOADING, STATUS_QUEUED, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_FAILED,
})

# 查询统计状态（``stats_status``）：导入写入的叶子分区在期次可见前要刷新一次规划器统计，
# 否则新期次不在统计里、被估成 1 行，查询退化成逐行嵌套循环（#148 实测 18–31 s，首次入榜
# 筛选 >120 s；刷新后 ≤1.3 s）。刷新失败不回滚数据，靠这个状态让失败可见、可被重试。
# NULL = 不适用（迁移前的旧批次、未成功导入的批次）。
STATS_PENDING = "PENDING"
STATS_READY = "READY"
STATS_FAILED = "FAILED"
"""重试次数用尽，不再自动重试；之后只能靠 autovacuum 或人工兜底。"""
STATS_MAX_ATTEMPTS = 3

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
        comment="UPLOADING（直传中）/ QUEUED（排队）/ PROCESSING / COMPLETED / FAILED",
    )

    total_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="源文件行数")
    written_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="实际写入事实表的行数")
    processing_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="查询统计状态：PENDING/READY/FAILED；NULL 为不适用（旧批次或未成功导入）",
    )
    stats_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0",
        comment="查询统计刷新已失败的次数，达到上限后置 FAILED 不再重试",
    )

    __table_args__ = (
        # 按站点追溯导入历史。
        Index("idx_import_batch_site_period", "site", "period_type", "report_date"),
        Index("idx_import_batch_status", "status"),
        Index("idx_import_batch_created", "created_at"),
        {"comment": "导入批次记录：每次导入可追溯到站点/周期/期次。status 含 QUEUED（导入串行，拿不到咨询锁时排队）。"},
    )
