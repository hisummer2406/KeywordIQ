"""category_stats brand_stats 补 report_date 索引

Revision ID: 47df76b2f424
Revises: 71e646b2d200
Create Date: 2026-09-17 17:20:56.815281

issue #100 PR #101 复审（P2）：保留清理新增的 `retention_service.purge_stale_stats`
按 `WHERE report_date < :cutoff` 清理这两张表的残留行，不带 `site`/`period_type`
等值条件。而这两张表的唯一索引是主键 `(site, period_type, report_date, id)`——
`report_date` 排在第三位，单独按它做范围条件用不上这个索引的前缀，退化成全表扫描。

代价不是"随残留月份数同阶"（`purge_stale_stats` 文档字符串原先这么写，是错的），是
随**整张表**的行数同阶：`filter_stats.py` 的注释记着品牌基数约 15.97 万/站/天，三个月
保留窗口、多站点叠起来是千万级行，每次保留清理（哪怕这次一行都不用清）都要扫一遍。

新增单列索引 `report_date`：这条清理只按 `report_date` 过滤，不需要 `site`/
`period_type` 打头——那两列的等值查询已经由主键覆盖，不重复建。仅新增索引，不改表
结构。

**这是本迁移唯一与数据量成正比的一步。** `category_stats`/`brand_stats` 不是分区表，
`CREATE INDEX CONCURRENTLY` 本可用，但 alembic 把整个迁移包在一个事务里、
`CONCURRENTLY` 不能在事务内跑，这里退回普通建索引，期间会持锁阻塞该表的读写。空库
（首次上线）秒级完成；已装满的库应当先离线补建，此时 `IF NOT EXISTS` 会空转而过：

    CREATE INDEX CONCURRENTLY idx_category_stats_report_date
        ON analysis.category_stats (report_date);
    CREATE INDEX CONCURRENTLY idx_brand_stats_report_date
        ON analysis.brand_stats (report_date);

写法与迁移 `9d4eb6834e6b`（fact_dropped 补关键词历史索引）同一模式。
"""
from collections.abc import Sequence

from alembic import op

from config import settings

# revision identifiers, used by Alembic.
revision: str = '47df76b2f424'
down_revision: str | None = '71e646b2d200'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_category_stats_report_date "
        f"ON {SCHEMA}.category_stats (report_date)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_brand_stats_report_date "
        f"ON {SCHEMA}.brand_stats (report_date)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_category_stats_report_date")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_brand_stats_report_date")
