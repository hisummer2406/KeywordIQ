"""迁移核对脚本的表清单单元测试（不连库）。

回归目标：`_TABLES_WITH_LATEST_COL` 曾漏掉 `fact_dropped`/`fact_growth`——两张表照常被
`pg_restore`，但核对脚本压根不看它们，即便迁移中途出错、这两张表少落了数据，核对仍会
全绿放行切换 `.env`（Codex 审核 PR #84）。断言与 `PARTITIONED_TABLES` 对齐，而不是
硬编码期望清单：新增分区事实表时若忘了同步进核对脚本，这条测试直接红，不必等到真实
迁移演练才发现。
"""

from app.table.analysis.partitioning import PARTITIONED_TABLES
from scripts.verify_selfhosted_migration import _TABLES_WITH_LATEST_COL


def test_核对清单覆盖全部分区事实表且日期列一致():
    checked = dict(_TABLES_WITH_LATEST_COL)

    for table, scheme in PARTITIONED_TABLES.items():
        assert table in checked, f"{table} 在 PARTITIONED_TABLES 里登记了，但迁移核对脚本没有核对它"
        assert checked[table] == scheme.date_column, (
            f"{table} 的核对列 {checked[table]!r} 与 PARTITIONED_TABLES 里的 "
            f"{scheme.date_column!r} 不一致"
        )
