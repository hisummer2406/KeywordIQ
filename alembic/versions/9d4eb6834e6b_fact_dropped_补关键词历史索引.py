"""fact_dropped 补关键词历史索引

Revision ID: 9d4eb6834e6b
Revises: ef43bf0d3c92
Create Date: 2026-09-08 13:04:00.548752

改接在 ef43bf0d3c92（user_center 加 avatar_url 列）之后而非原先的 533cd79ca75f：
两者在本分支拉出后分别落在 v2 上，形成同一父节点下的两个 head（PR #87 复审第二轮
CI 报的 "Multiple head revisions"）。这里选择线性重接而非 `alembic merge` 产生
的双 down_revision 合并节点——仓库迄今没有任何一条迁移用过合并节点，CI 的「降一步
再升回来」步骤（ci.yml「迁移可回滚性」）用的是不带具体目标的 `alembic downgrade
-1`，在合并节点处哪一条父分支是"-1"是歧义的（`Ambiguous walk`）。两条迁移改动的
表互不相关（这条动索引，对方加列），先后顺序不影响正确性，线性重接不需要连带改
CI 脚本。

PR #87 Codex 审核（P1）：趋势详情的 `keyword_dropped_stmt` 走 `fact_dropped` 主键
`(site, period_type, anchor_date, keyword_id)`。一旦 `anchor_date` 是范围而非等值
（30 天窗口、更别提"全部可用历史"），`keyword_id` 就退化成扫描内 filter——代价随该
站点/视图/日期窗口内的掉榜行数增长，不随目标关键词的历史行数增长，违背 proposal 里
「查询只按单个 keyword_id 的历史索引取数，不扫全站榜单」的设计前提。

新增 `idx_fact_dropped_keyword_history (site, period_type, keyword_id, anchor_date)`：
把 keyword_id 放到 anchor_date 之前，让等值条件先把范围收窄到单个关键词，anchor_date
范围再在这个子集内扫描。仅新增索引，不改表结构，`fact_dropped` 的分区方案不受影响
——在父表（分区表）上建索引会被 PostgreSQL 递归应用到全部现有叶子分区，`partitioning.
ensure_fact_partitions` 之后新建的分区同样自动带有该索引，无需在这里或那个函数里
额外处理。

**这是本迁移唯一与数据量成正比的一步。** `CREATE INDEX IF NOT EXISTS` 是父表层面的
操作；分区父表的 `CREATE INDEX` 不支持 `CONCURRENTLY`，alembic 又把整个迁移包在一个
事务里——空库（首次上线）秒级完成，但已装满的库上这一步会长时间持锁，期间
`fact_dropped` 的读写全部阻塞（写法与迁移 `7b8b046c075e`/`d3e4f5a6b7c8` 第 3.5 步同
一模式）。已装满的库应当先离线补建再跑本迁移，此时 `IF NOT EXISTS` 会空转而过：

    -- 1) 父表上建"空壳"索引，只改 catalog，不读数据
    CREATE INDEX idx_fact_dropped_keyword_history ON ONLY analysis.fact_dropped
           (site, period_type, keyword_id, anchor_date);
    -- 2) 逐个叶子分区并发建——fact_dropped 只有一级 RANGE(anchor_date) 月度分区、
    --    没有 fact_daily/fact_weekly 那层站点子分区，叶子就是月分区，不需要中间的
    --    ONLY + ATTACH 层
    CREATE INDEX CONCURRENTLY fact_dropped_2026_07_keyword_history
        ON analysis.fact_dropped_2026_07 (site, period_type, keyword_id, anchor_date);
    -- 3) 挂回去；全部叶子分区挂齐后父索引自动转为 valid
    ALTER INDEX analysis.idx_fact_dropped_keyword_history
        ATTACH PARTITION analysis.fact_dropped_2026_07_keyword_history;

其余保留窗口内的月份分区依此类推。
"""
from collections.abc import Sequence

from alembic import op
from config import settings

# revision identifiers, used by Alembic.
revision: str = '9d4eb6834e6b'
down_revision: str | None = 'ef43bf0d3c92'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_fact_dropped_keyword_history "
        f"ON {SCHEMA}.fact_dropped (site, period_type, keyword_id, anchor_date)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_fact_dropped_keyword_history")
