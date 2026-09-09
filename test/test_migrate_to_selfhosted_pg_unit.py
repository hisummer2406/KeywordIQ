"""`migrate_to_selfhosted_pg._reset_target_tables` 的命令构造单元测试（不连库）。

回归目标：`pg_restore --data-only` 不是幂等操作，失败重跑会在已恢复过的表上撞主键
冲突（Codex 审核 PR #84 第十四轮）。`_reset_target_tables` 在恢复之前无条件清空目标
表让重跑安全；这里不起真实容器，只断言它拼出的 SQL/命令形状正确：
（1）dry-run 不发任何命令；（2）列表查询只用于枚举，TRUNCATE 用同一批表名，且种子表
（`_MIGRATION_SEEDED_TABLES`）不在其中；（3）目标库里没有非种子表时不发 TRUNCATE。
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
