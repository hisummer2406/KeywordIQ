"""迁移前置分区脚本（`ensure_target_partitions_for_migration.py`）的数据库集成测试。

单元测试测不出这个问题：`_source_leaf_partitions` 靠 PostgreSQL 的目录函数
`pg_partition_tree` 直接枚举叶子分区，行为要在真实数据库上验证。

回归目标：`fact_dropped`/`fact_growth` 的叶子分区在 `import_report` 里先建、后计算
掉榜/增长行，即便某一期结果为空也照常提交——源库因此可能存在"分区已建、一行没有"的
空叶子。旧的行驱动写法（`SELECT DISTINCT ... FROM analysis.{table}`）反推月份时看不到
这种空叶子；`pg_dump --data-only` 仍会为它发一条 COPY 归档项，目标库没建这个分区，
`pg_restore` 就会在 RDS 已切只读之后才报 relation 不存在（Codex 审核 PR #84 第七轮）。

**默认跳过**，仅在设置 `TEST_DATABASE_URL` 时运行。见 test/test_v2_integration.py
同款说明与 .claude/skills/local-db/SKILL.md。
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.table.analysis.partitioning import ensure_fact_partitions
from scripts.ensure_target_partitions_for_migration import _FALLBACK_SITE, _source_leaf_partitions

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def test_识别行数为零的一级叶子分区():
    """fact_dropped 只做一级月度分区（site_subpartition=False）。"""
    engine = create_engine(TEST_URL)
    Session = sessionmaker(bind=engine)
    anchor = date(2031, 3, 15)  # 远期日期避开与其他测试/真实数据的月份冲突

    try:
        with Session() as session:
            ensure_fact_partitions(session, "fact_dropped", _FALLBACK_SITE, anchor)
            session.commit()

        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM analysis.fact_dropped_2031_03")).scalar()
        assert count == 0, "本测试要覆盖的正是「分区已建、一行没有」这个场景"

        found = _source_leaf_partitions(TEST_URL, "fact_dropped", has_site=False)
        assert (_FALLBACK_SITE, "2031-03-01") in found
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS analysis.fact_dropped_2031_03"))


def test_识别两级分区的站点叶子且行数为零():
    """fact_daily 做两级分区（月度 RANGE + 站点 LIST），核对站点后缀解析正确。"""
    engine = create_engine(TEST_URL)
    Session = sessionmaker(bind=engine)
    anchor = date(2031, 5, 10)

    try:
        with Session() as session:
            ensure_fact_partitions(session, "fact_daily", "GB", anchor)
            session.commit()

        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM analysis.fact_daily_2031_05_gb")).scalar()
        assert count == 0

        found = _source_leaf_partitions(TEST_URL, "fact_daily", has_site=True)
        assert ("GB", "2031-05-01") in found
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS analysis.fact_daily_2031_05_gb"))
            conn.execute(text("DROP TABLE IF EXISTS analysis.fact_daily_2031_05"))
