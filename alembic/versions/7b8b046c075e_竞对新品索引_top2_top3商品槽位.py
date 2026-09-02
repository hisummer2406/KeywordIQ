"""竞对新品索引：top2/top3 商品槽位

Revision ID: 7b8b046c075e
Revises: ec4b6e130f5f
Create Date: 2026-09-02 01:16:00.188491

**手写迁移**：涉及分区表，autogenerate 看不见分区，会把事实表当普通表处理、静默丢掉
`PARTITION BY`。写法照抄迁移 `d3e4f5a6b7c8`（品牌类目改为关键词级维度）第 3.5 步——
两张事实表各补 `top2_product_id`/`top3_product_id` 索引，与已有的
`idx_{prefix}_top1_product`/三个品牌索引同构（列序 `(site, date_col, topN_product_id)`）。

**这是本迁移唯一与数据量成正比的一步。** `CREATE INDEX IF NOT EXISTS` 是父表层面的
操作；分区父表的 `CREATE INDEX` 不支持 `CONCURRENTLY`，alembic 又把整个迁移包在一个
事务里——空库（首次上线）秒级完成，但已装满的库上这一步会长时间持锁，期间事实表的读写
全部阻塞。已装满的库应当先离线补建再跑本迁移，此时 `IF NOT EXISTS` 会空转而过：

    -- 1) 父表上建"空壳"索引，只改 catalog，不读数据
    CREATE INDEX idx_fact_daily_top2_product ON ONLY analysis.fact_daily
           (site, report_date, top2_product_id);
    -- 2) 逐个叶子分区并发建（三层结构，叶子是站点子分区 fact_daily_2026_07_us）
    CREATE INDEX CONCURRENTLY fact_daily_2026_07_us_top2_product
        ON analysis.fact_daily_2026_07_us (site, report_date, top2_product_id);
    -- 3) 挂回去；全部子分区挂齐后父索引自动转为 valid
    ALTER INDEX analysis.idx_fact_daily_top2_product
        ATTACH PARTITION analysis.fact_daily_2026_07_us_top2_product;

中间层（月分区 `fact_daily_2026_07`）同样要走 ONLY + ATTACH 这一层。`top3_product_id`
与 `fact_weekly`（日期列是 `report_date_week` 不是 `report_date`）依此类推。
"""
from typing import Sequence, Union

from alembic import op

from config import settings

# revision identifiers, used by Alembic.
revision: str = '7b8b046c075e'
down_revision: Union[str, None] = 'ec4b6e130f5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    for table, date_col in (("fact_daily", "report_date"), ("fact_weekly", "report_date_week")):
        for n in (2, 3):
            op.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_top{n}_product "
                f"ON {SCHEMA}.{table} (site, {date_col}, top{n}_product_id)"
            )


def downgrade() -> None:
    for table in ("fact_daily", "fact_weekly"):
        for n in (2, 3):
            op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_{table}_top{n}_product")
