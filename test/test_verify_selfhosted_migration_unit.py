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

回归目标三：`category_stats`/`brand_stats` 不在 `V2_TABLES` 里——它们没有 ORM 模型，
全靠原生 SQL 写入（`app/table/analysis/filter_stats.py`），因此不受 alembic
autogenerate 管理，也就不登记进 `V2_TABLES`。但它们仍是 `analysis` schema 下的普通表
（迁移 `f5a6b7c8d9e0` 已从物化视图改成普通表），`pg_dump --data-only --schema=analysis`
按整个 schema 搬迁、不看 `V2_TABLES`，同样会把它们搬过去；核对清单若只从 `V2_TABLES`
派生就会漏掉这两张，而 `analysis_crud.py` 靠它们提供类目/品牌下拉（Codex 审核
PR #84 第七轮）。断言因此把 `_FILTER_STATS`（该模块实际写入的表）并入预期集合，
而不是把这两个表名重新手写一遍。
"""

from app.table.analysis.filter_stats import _FILTER_STATS
from app.table.analysis.partitioning import PARTITIONED_TABLES
from migration_scope import V2_TABLES
from scripts.migrate_to_selfhosted_pg import _MIGRATION_SEEDED_TABLES
from scripts.verify_selfhosted_migration import _TABLES_WITH_LATEST_COL, _table_ok


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
    non_orm_migrated_tables = {table for table, _, _ in _FILTER_STATS}
    migrated = (V2_TABLES - set(_MIGRATION_SEEDED_TABLES)) | non_orm_migrated_tables

    missing = migrated - checked
    assert not missing, f"以下表被 pg_dump --data-only 搬迁，但迁移核对脚本没有核对: {sorted(missing)}"

    extra = checked - migrated
    assert not extra, (
        f"以下表在核对脚本里但不在 V2_TABLES（或属于种子表）: {sorted(extra)}——"
        "核对了一张不会被迁移搬迁的表，源库/目标库的计数必然不可比"
    )


class Test观察窗口模式的比较判据:
    """`_table_ok(..., observation_window=True)` 的回归。

    背景：观察窗口内 RDS 已被冻结为拒绝新写（迁移基准恒定不变），自建库在正常
    服务、行数与最新期次只会比基准更多/更新。若沿用默认的严格相等比较，窗口内
    第一条真实写入就会让核对报"不一致"，把预期之内的增长误判成搬迁不完整
    （Codex 审核 PR #84 第二十三轮）。
    """

    def test_目标库行数与最新期次都比基准更新时通过(self):
        assert _table_ok(100, "2026-09-01", 130, "2026-09-14", observation_window=True)

    def test_目标库与基准完全相同时通过(self):
        assert _table_ok(100, "2026-09-01", 100, "2026-09-01", observation_window=True)

    def test_目标库行数反而少于基准时不通过(self):
        assert not _table_ok(100, "2026-09-01", 90, "2026-09-14", observation_window=True)

    def test_目标库最新期次反而早于基准时不通过(self):
        assert not _table_ok(100, "2026-09-14", 130, "2026-09-01", observation_window=True)

    def test_基准当时为空而目标库此后长出数据时通过(self):
        """源库这张表在冻结时是空的（latest=None），目标库此后写入不算落后。"""
        assert _table_ok(0, None, 5, "2026-09-14", observation_window=True)

    def test_基准当时有数据而目标库反而是空的时不通过(self):
        """这是真正的倒退——搬迁本就不完整，不能被观察窗口模式的宽松判据放过。"""
        assert not _table_ok(5, "2026-09-01", 0, None, observation_window=True)

    def test_两边都为空时通过(self):
        assert _table_ok(0, None, 0, None, observation_window=True)

    def test_默认模式仍要求严格相等不受观察窗口判据影响(self):
        """迁移前的一次性门禁（observation_window=False）不能因为这个新参数被放宽——
        目标库此刻还没开始服务，"比基准更多"本身就意味着搬迁过程本身有问题。"""
        assert not _table_ok(100, "2026-09-01", 130, "2026-09-14", observation_window=False)
        assert _table_ok(100, "2026-09-01", 100, "2026-09-01", observation_window=False)
