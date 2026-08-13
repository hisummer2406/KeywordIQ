"""V2 数据模型的数据库集成测试——验证结构在真实 PostgreSQL 上的行为。

**这些是单元测试验证不了的**：分区表是否真的按分区裁剪、跨月是否自动建分区、
唯一约束是否真的拦得住重复、"确保分区存在"函数是否幂等。单元测试只能看模型元数据，
看不到 PostgreSQL 的实际执行。

**默认跳过，永不误触生产。** 只在设置了 ``TEST_DATABASE_URL`` 时运行，且该库必须
是一次性的测试库（每个测试在事务里跑完即回滚，不留痕迹，但仍建议用专用库）。

运行方式::

    # 先把 schema 迁移到测试库
    DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run alembic upgrade head
    # 再跑集成测试
    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_v2_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


@pytest.fixture()
def conn():
    """每个测试独占一个事务，结束即回滚——包括 DDL（PostgreSQL 的建表可回滚），
    因此测试之间零残留，也不污染库。"""
    engine = create_engine(TEST_URL)
    connection = engine.connect()
    trans = connection.begin()
    # 前置检查：schema 必须已迁移，否则给出可操作的提示而非晦涩报错。
    exists = connection.execute(
        text("SELECT to_regclass('analysis.fact_daily')")
    ).scalar()
    if exists is None:
        trans.rollback()
        connection.close()
        engine.dispose()
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()
        engine.dispose()


def _ensure(conn, table, site, d):
    from app.table.analysis.partitioning import ensure_fact_partitions
    ensure_fact_partitions(conn, table, site, d)


def _add_keyword(conn, site, keyword) -> int:
    return conn.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:k) RETURNING keyword_id"),
        {"s": site, "k": keyword},
    ).scalar()


def _add_fact(conn, site, d, keyword_id, ranking):
    conn.execute(
        text("""INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking)
                VALUES (:s,:d,:k,:r)"""),
        {"s": site, "d": d, "k": keyword_id, "r": ranking},
    )


def _leaf_partitions(conn, parent) -> list[str]:
    return conn.execute(
        text("""
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = :parent ORDER BY c.relname
        """),
        {"parent": parent},
    ).scalars().all()


# --------------------------------------------------------------------------


def test_fact_tables_are_partitioned(conn):
    """迁移必须把事实表建成分区表，而不是普通表。这是离线 SQL 验证不了、
    autogenerate 会静默搞错的地方。"""
    for table in ("fact_daily", "fact_weekly"):
        strategy = conn.execute(
            text("""SELECT partstrat FROM pg_partitioned_table pt
                    JOIN pg_class c ON c.oid = pt.partrelid
                    WHERE c.relname = :t"""),
            {"t": table},
        ).scalar()
        assert strategy == "r", f"{table} 不是 RANGE 分区表（partstrat={strategy!r}）"


def test_ensure_partition_is_idempotent(conn):
    """重复调用不报错、不重复创建——导入前会无条件调用它。"""
    _ensure(conn, "fact_daily", "US", date(2026, 7, 15))
    _ensure(conn, "fact_daily", "US", date(2026, 7, 15))  # 第二次
    parts = _leaf_partitions(conn, "fact_daily_2026_07")
    assert parts.count("fact_daily_2026_07_us") == 1


def test_cross_month_auto_creates_partition(conn):
    """报告日期落在新月份时，分区被自动创建——跨月首次导入不能因分区缺失而失败。"""
    _ensure(conn, "fact_daily", "US", date(2026, 7, 15))
    assert "fact_daily_2026_08" not in _leaf_partitions(conn, "fact_daily")
    _ensure(conn, "fact_daily", "US", date(2026, 8, 3))  # 跨到 8 月
    assert "fact_daily_2026_08" in _leaf_partitions(conn, "fact_daily")


def test_unique_constraint_blocks_duplicate(conn):
    """同 (site, date, keyword) 不可重复。"""
    from sqlalchemy.exc import IntegrityError
    _ensure(conn, "fact_daily", "US", date(2026, 7, 15))
    kid = _add_keyword(conn, "US", "wireless mouse")
    _add_fact(conn, "US", date(2026, 7, 15), kid, 10)
    with pytest.raises(IntegrityError):
        _add_fact(conn, "US", date(2026, 7, 15), kid, 99)


def test_same_keyword_coexists_across_site_and_date(conn):
    """同一关键词文本在不同站点、不同日期各自独立共存。"""
    _ensure(conn, "fact_daily", "US", date(2026, 7, 15))
    _ensure(conn, "fact_daily", "GB", date(2026, 7, 15))
    _ensure(conn, "fact_daily", "US", date(2026, 7, 16))
    us_kid = _add_keyword(conn, "US", "wireless mouse")
    gb_kid = _add_keyword(conn, "GB", "wireless mouse")  # 同词，不同站点 → 不同维度行
    assert us_kid != gb_kid
    _add_fact(conn, "US", date(2026, 7, 15), us_kid, 10)
    _add_fact(conn, "GB", date(2026, 7, 15), gb_kid, 5)
    _add_fact(conn, "US", date(2026, 7, 16), us_kid, 8)  # 同站同词，隔天
    count = conn.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar()
    assert count == 3


def test_partition_pruning_touches_single_leaf(conn):
    """单站单日查询的执行计划只应触及一个叶子分区（站点物理隔离 + 月分区裁剪）。"""
    for d in (date(2026, 7, 15), date(2026, 8, 3)):
        _ensure(conn, "fact_daily", "US", d)
        _ensure(conn, "fact_daily", "GB", d)
    plan = "\n".join(
        r[0] for r in conn.execute(text(
            "EXPLAIN SELECT * FROM analysis.fact_daily WHERE site='US' AND report_date='2026-07-15'"
        ))
    )
    # 计划里不得出现其他月份或其他站点的分区。
    assert "2026_08" not in plan, f"未按月裁剪，计划触及了 8 月分区:\n{plan}"
    assert "_gb" not in plan, f"未按站点裁剪，计划触及了 GB 分区:\n{plan}"
    assert "fact_daily_2026_07_us" in plan, f"未命中目标叶子分区:\n{plan}"


def test_models_match_migrated_schema(conn):
    """模型与迁移到 head 的库之间不得有任何 autogenerate 差异。

    这条守的是「autogenerate 的输出是信号，不是噪音」。库里的注释与模型对不上时，
    一次 autogenerate 会吐出几十条纯注释的 alter_column，**真正的结构差异就淹在里面**
    ——``user_center.created_at/updated_at`` 在模型里是无时区 DateTime、库里是
    timestamptz，这条就那样躲了很久。注释差异在这里同样算失败，正是因为放任它们
    就会重新把噪音养起来。

    失败时 diff 会直接列出差异项：改模型或补一条迁移，二选一。
    """
    import alembic.config
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    # 必须逐个 import 模型模块，与 alembic/env.py 做的事一样：模型是靠 import 的副作用
    # 注册到 Base.metadata 的。漏掉就是一份空 metadata，比对结果会是「库里每张表都多余」
    # ——一屏 remove_table，看起来像天塌了，实际只是没导入。
    from app.table.analysis import dimension_model, fact_model  # noqa: F401
    from app.table.upload import import_batch_model  # noqa: F401
    from app.user import user_model  # noqa: F401
    from database import Base
    from migration_scope import include_object

    # 库落后于 head 时差异必然存在，但那说明的是「测试库该迁移了」，不是模型有问题。
    cfg = alembic.config.Config(os.path.join(REPO, "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    current = conn.execute(
        text("SELECT version_num FROM analysis.alembic_version")
    ).scalar()
    if current != head:
        pytest.skip(f"测试库在 {current}，head 是 {head}，请先 alembic upgrade head")

    ctx = MigrationContext.configure(
        connection=conn,
        opts={
            "include_schemas": True,
            "include_object": include_object,
            "compare_type": True,
            "version_table_schema": "analysis",
        },
    )
    diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == [], (
        "模型与库不一致，autogenerate 会生成这些操作：\n"
        + "\n".join(f"  - {d}" for d in diffs)
    )
