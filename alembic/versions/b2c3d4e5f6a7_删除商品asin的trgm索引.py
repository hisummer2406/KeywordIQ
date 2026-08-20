"""删除 dim_product.asin 的 trigram 索引（ASIN 改为等值匹配）

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-20

issue #49 第 3 项。`idx_dim_product_asin_trgm` 服务的是 `asin ILIKE '%...%'` 这种子串
检索，而 **ASIN 是精确码**：界面上它只能从顶部检索框输入，那里只有整串匹配
`^B0[A-Z0-9]{8}$` 才会路由到 asin 字段——片段本来就到不了后端。筛选随本次改为等值匹配，
走 `uq_dim_product_asin` 唯一索引即可，这个 GIN 索引再无查询使用。

**它不是躺着不动的存储开销。** GIN 索引由每次导入的每一行写入维护：一份日报约 40 万行
× 3 个商品槽位 ≈ 120 万个 ASIN 提及都要过它一遍，而生产上它是 409 MB。`dim_product`
只增不减（保留清理只 DROP 事实表分区，维度表不参与），所以这笔开销会继续长。

**加了 `lock_timeout`。** DROP INDEX 本身很快（改目录 + 删文件），但它要 ACCESS
EXCLUSIVE 锁；若此刻有导出的服务端游标正扫着 `dim_product`，DROP 会排队等锁，而排在它
后面的**所有** dim_product 读请求一并被堵住——一次本该毫秒级的操作变成整个查询屏卡死。
超时失败只是「换个时段再来一次」，比那个后果轻得多。

降级重建索引，用 `CONCURRENTLY` 不可行（不能在事务块里跑），因此重建期间同样持锁；
但降级本就是计划内操作，可自行择时。
"""
from typing import Sequence, Union

from alembic import op

from config import settings

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = settings.DATABASE_SCHEMA


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.idx_dim_product_asin_trgm")


def downgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_dim_product_asin_trgm "
        f"ON {SCHEMA}.dim_product USING gin (asin gin_trgm_ops)"
    )
