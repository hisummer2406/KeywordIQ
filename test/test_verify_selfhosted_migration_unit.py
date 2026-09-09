"""迁移核对脚本的表清单单元测试（不连库）。

回归目标一：`_TABLES_WITH_LATEST_COL` 曾漏掉 `fact_dropped`/`fact_growth`——两张表照常被
`pg_restore`，但核对脚本压根不看它们，即便迁移中途出错、这两张表少落了数据，核对仍会
全绿放行切换 `.env`（Codex 审核 PR #84）。断言与 `PARTITIONED_TABLES` 对齐，而不是
硬编码期望清单：新增分区事实表时若忘了同步进核对脚本，这条测试直接红，不必等到真实
迁移演练才发现。

回归目标二：核对清单曾只列 7 张表（四张事实表 + `dim_keyword`/`dim_product`/
`site_data_stats`），`user_center`、词库、`import_batch`、全部 `competitor_*` 共享表
与关注表都不在其中——这些表若在 `pg_restore` 时少落了数据，核对同样全绿放行切换，
实际后果是用户登不进去、收藏的词库/关注清单凭空消失（Codex 审核 PR #84 第五轮）。
断言与 `migration_scope.V2_TABLES` 对齐——这是 alembic 用来判定"哪些表归 V2 管"的
同一份权威清单，减去只由迁移种子写入、不经 `pg_dump --data-only` 搬迁的种子表
（`migrate_to_selfhosted_pg._MIGRATION_SEEDED_TABLES`）——而不是另写一份期望表名，
新模型登记进 `V2_TABLES` 后若忘了同步进核对脚本，这条测试直接红。
"""

from app.table.analysis.partitioning import PARTITIONED_TABLES
from migration_scope import V2_TABLES
from scripts.migrate_to_selfhosted_pg import _MIGRATION_SEEDED_TABLES
from scripts.verify_selfhosted_migration import _TABLES_WITH_LATEST_COL


def test_核对清单覆盖全部分区事实表且日期列一致():
    checked = dict(_TABLES_WITH_LATEST_COL)

    for table, scheme in PARTITIONED_TABLES.items():
        assert table in checked, f"{table} 在 PARTITIONED_TABLES 里登记了，但迁移核对脚本没有核对它"
        assert checked[table] == scheme.date_column, (
            f"{table} 的核对列 {checked[table]!r} 与 PARTITIONED_TABLES 里的 "
            f"{scheme.date_column!r} 不一致"
        )


def test_核对清单覆盖全部迁移搬迁的持久表():
    checked = set(dict(_TABLES_WITH_LATEST_COL).keys())
    migrated = V2_TABLES - set(_MIGRATION_SEEDED_TABLES)

    missing = migrated - checked
    assert not missing, f"以下表被 pg_dump --data-only 搬迁，但迁移核对脚本没有核对: {sorted(missing)}"

    extra = checked - migrated
    assert not extra, (
        f"以下表在核对脚本里但不在 V2_TABLES（或属于种子表）: {sorted(extra)}——"
        "核对了一张不会被迁移搬迁的表，源库/目标库的计数必然不可比"
    )
