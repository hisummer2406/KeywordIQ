"""`migrate_to_selfhosted_pg._reset_target_tables` 的命令构造单元测试（不连库）。

回归目标：`pg_restore --data-only` 不是幂等操作，失败重跑会在已恢复过的表上撞主键
冲突（Codex 审核 PR #84 第十四轮）。`_reset_target_tables` 在恢复之前无条件清空目标
表让重跑安全；这里不起真实容器，只断言它拼出的 SQL/命令形状正确：
（1）dry-run 不发任何命令；（2）列表查询只用于枚举，TRUNCATE 用同一批表名，且种子表
（`_MIGRATION_SEEDED_TABLES`）不在其中；（3）目标库里没有非种子表时不发 TRUNCATE；
（4）列表查询读 `pg_class` 并按 `relispartition` 过滤，不再读 `pg_tables`——后者会把
分区叶子（如 `fact_daily_2026_01_US`）和顶层表混在一起列出（Codex 审核 PR #84
第十六轮）。这条只保护查询*形状*不回退，不是行为回归测试：分区叶子会不会真的造成
`_reset_target_tables` 失败，只有连真实分区表的库才能验证——已用一次性
`postgres:15.8` 容器复刻 `fact_daily` 的两级分区结构实测过，结论是父表与各级叶子
混排在同一条 TRUNCATE 里（任意顺序、带/不带 CASCADE）并不会报错，PostgreSQL 会
自行去重；这里改用 `relispartition` 过滤是让查询符合函数自己文档一直声称的
"不需要单独枚举分区"，不是修一个会导致失败的缺陷（该容器验证结束后已清理，不在本
仓库留任何脚本或产物）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.migrate_to_selfhosted_pg import _MIGRATION_SEEDED_TABLES, _reset_target_tables


def test_dry_run不发任何命令():
    with patch("scripts.migrate_to_selfhosted_pg.subprocess.run") as run:
        _reset_target_tables("db", "amazon_search", dry_run=True)
    run.assert_not_called()


def test_truncate只覆盖列表查询返回的表且排除种子表():
    list_result = MagicMock(stdout="dim_keyword\nfact_daily\n")
    truncate_result = MagicMock()
    with patch(
        "scripts.migrate_to_selfhosted_pg.subprocess.run",
        side_effect=[list_result, truncate_result],
    ) as run:
        _reset_target_tables("db", "amazon_search", dry_run=False)

    assert run.call_count == 2

    list_cmd = run.call_args_list[0].args[0]
    list_sql = list_cmd[-1]
    assert "pg_class" in list_sql
    assert "relispartition" in list_sql
    assert "pg_tables" not in list_sql
    for seeded in _MIGRATION_SEEDED_TABLES:
        assert f"'{seeded}'" in list_sql

    truncate_cmd = run.call_args_list[1].args[0]
    truncate_sql = truncate_cmd[-1]
    assert truncate_sql.startswith("TRUNCATE ")
    assert truncate_sql.endswith(" CASCADE")
    assert "analysis.dim_keyword" in truncate_sql
    assert "analysis.fact_daily" in truncate_sql
    for seeded in _MIGRATION_SEEDED_TABLES:
        assert f"analysis.{seeded}" not in truncate_sql


def test_没有非种子表时不发truncate():
    list_result = MagicMock(stdout="")
    with patch(
        "scripts.migrate_to_selfhosted_pg.subprocess.run", side_effect=[list_result]
    ) as run:
        _reset_target_tables("db", "amazon_search", dry_run=False)

    run.assert_called_once()
