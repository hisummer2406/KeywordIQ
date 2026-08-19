"""持续增长榜算定（`importer.load_growth`）的数据库集成测试。

这条路径的口径是一次窗口聚合 + 一组 HAVING，**每个边界都只差一个数**：环比 9.9% 与
10%、净增 9999 与 10001、窗口内在榜 6 期与 7 期。算错任何一个都不会报错，只会让榜单
悄悄多几行或少几行——而榜单是用来做选品决策的，没人能从结果反推出口径写错了。所以这里
把每个阈值的两侧各钉一个用例。

窗口聚合与 HAVING 无法用单元测试替代（lag / first_value / FILTER 都是库内语义）。

默认跳过，只在设置 ``TEST_DATABASE_URL`` 时运行。测试库必须已迁移。每个测试独占事务、
结束回滚，零残留。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_growth_calc_integration.py -v
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.table.analysis.partitioning import ensure_fact_partitions
from app.table.upload.importer import load_growth
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行增长榜集成测试")

@pytest.fixture(autouse=True)
def _pinned_growth_thresholds(monkeypatch):
    """把口径阈值钉在默认值上，不随本机 .env 漂移。

    这些用例的全部意义是「每个阈值的两侧各钉一个」——夹具造的数据精确卡在 9.9% 与
    10% 之间。而 `load_growth` 与作用域端点都在调用时读 `settings.GROWTH_*`，于是本机
    .env 只要覆盖了阈值（开发机上确实有人把 GROWTH_MIN_DAILY_RATE 调成 0.05 做过实验），
    这些用例就稳定失败，而 CI 因为没有 .env 一直是绿的（issue #41）。

    失败信息完全指不到成因：看到的是"这个词不该在榜上却在榜上"，不会想到是环境变量。
    """
    monkeypatch.setattr(settings, "GROWTH_WINDOW_DAYS", 7)
    monkeypatch.setattr(settings, "GROWTH_MIN_RANK_GAIN", 10000)
    monkeypatch.setattr(settings, "GROWTH_MIN_DAILY_RATE", 0.10)


SITE = "US"
ANCHOR = date(2026, 7, 15)
# 窗口 [d-6, d]，七期。索引 0 是最早的一期。
WINDOW = [ANCHOR - timedelta(days=6 - i) for i in range(7)]


@pytest.fixture()
def session():
    engine = create_engine(TEST_URL)
    # 分区 DDL 在**自己的连接上**提交：幂等且本就该持久存在，跟着测试回滚掉会让下一次
    # 首跑失败（这个形态在本仓库真实发生过——首跑红、重跑绿，因为失败那次已把 DDL 提交）。
    with engine.begin() as ddl:
        if ddl.execute(text("SELECT to_regclass('analysis.fact_growth')")).scalar() is None:
            engine.dispose()
            pytest.skip("测试库未迁移到含 fact_growth 的版本，请先 alembic upgrade head")
        ensure_fact_partitions(ddl, "fact_daily", SITE, WINDOW[0])
        ensure_fact_partitions(ddl, "fact_daily", SITE, ANCHOR)
        ensure_fact_partitions(ddl, "fact_growth", SITE, ANCHOR)

    conn = engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn)
    try:
        yield sess
    finally:
        sess.close()
        trans.rollback()
        conn.close()
        engine.dispose()


def _seed(session: Session, keyword: str, rankings: dict[date, int]) -> int:
    """造一个关键词及其若干期排名。返回 keyword_id。

    只写指定的期次——某期不给排名就等于那天它没在榜，这正是「中途掉榜」的造法。
    """
    kid = session.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES (:s,:kw) RETURNING keyword_id"),
        {"s": SITE, "kw": keyword},
    ).scalar()
    for d, rank in rankings.items():
        session.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES (:s,:d,:k,:r)"),
            {"s": SITE, "d": d, "k": kid, "r": rank},
        )
    return kid


def _decay(start: int, rates: list[float]) -> dict[date, int]:
    """按给定的六个日环比生成七期排名。

    从 ``start`` 出发，每期按 ``rank * (1 - rate)`` 取整前进。用生成而不是手写常量，
    是为了让「第 k 天的环比恰好是 9.9%」这种用例的意图直接写在参数里。
    """
    ranks = [start]
    for rate in rates:
        ranks.append(round(ranks[-1] * (1 - rate)))
    return dict(zip(WINDOW, ranks, strict=True))


def _listed(session: Session, keyword: str) -> bool:
    """该关键词是否出现在锚定日的榜单里。作用域必须带全——裸 count 只在空库上成立。"""
    return bool(
        session.execute(
            text(
                """
                SELECT 1 FROM analysis.fact_growth g
                  JOIN analysis.dim_keyword k USING (keyword_id)
                 WHERE g.site = :s AND g.anchor_date = :d AND k.keyword = :kw
                """
            ),
            {"s": SITE, "d": ANCHOR, "kw": keyword},
        ).scalar()
    )


def _row(session: Session, keyword: str):
    return session.execute(
        text(
            """
            SELECT g.start_ranking, g.end_ranking, g.rank_gain, g.min_growth_rate
              FROM analysis.fact_growth g
              JOIN analysis.dim_keyword k USING (keyword_id)
             WHERE g.site = :s AND g.anchor_date = :d AND k.keyword = :kw
            """
        ),
        {"s": SITE, "d": ANCHOR, "kw": keyword},
    ).one()


# 每期前进 15%，从 6 万名出发：净增远超 1 万，环比全部达标。
_HIT_RATES = [0.15] * 6


def test_命中三条口径的词进榜(session):
    _seed(session, "hit", _decay(60000, _HIT_RATES))

    assert load_growth(session, SITE, ANCHOR) == 1

    start, end, gain, min_rate = _row(session, "hit")
    assert start == 60000
    assert gain == start - end
    assert gain > 10000
    assert float(min_rate) >= 0.10


def test_某一天环比差一点就出局(session):
    # 其余五天 15%，只有第三天 9.9%——最低环比因此是 0.099，差 0.001 不达标
    rates = [0.15, 0.15, 0.099, 0.15, 0.15, 0.15]
    _seed(session, "slow-day", _decay(60000, rates))

    load_growth(session, SITE, ANCHOR)

    assert not _listed(session, "slow-day")


def test_净增差一名的两侧(session):
    """净增 9999 出局、10001 进榜，两个词的日环比完全一样。

    成对造是必须的：只造一个「不进榜」的词，测试在环比条件也不满足时照样是绿的——那种
    绿说明不了净增这条阈值有没有生效。这里两个词共用同一条尾部路径，唯一的差别就是起点
    差 2 名，于是「谁被挡下来」只可能是净增决定的。
    """
    tail = [18850, 16920, 15188, 13634, 12238, 11001]  # 六步环比均 ≥10.1%
    _seed(session, "gain-9999", dict(zip(WINDOW, [21000, *tail], strict=True)))
    _seed(session, "gain-10001", dict(zip(WINDOW, [21002, *tail], strict=True)))

    assert load_growth(session, SITE, ANCHOR) == 1

    assert not _listed(session, "gain-9999")
    assert _row(session, "gain-10001")[2] == 10001


def test_窗口中途掉榜出局(session):
    ranks = _decay(60000, _HIT_RATES)
    del ranks[WINDOW[3]]  # 第四天掉出榜单：那天没有 fact_daily 行
    _seed(session, "gap", ranks)

    load_growth(session, SITE, ANCHOR)

    # 只在榜 6 期，凑不满 7。系统不得把缺失当 0，也不得跳过该期继续接连。
    assert not _listed(session, "gap")


def test_排名为零的脏行不算在榜(session):
    ranks = _decay(60000, _HIT_RATES)
    ranks[WINDOW[2]] = 0  # 排名 0 是清洗后的脏值，不是一条有效榜单记录
    _seed(session, "zero-rank", ranks)

    # 不炸（0 若参与日环比会让分母为零直接打断整个导入），且该词不进榜
    load_growth(session, SITE, ANCHOR)

    assert not _listed(session, "zero-rank")


def test_窗口缺一期时整期榜单为空(session):
    """整期未导入 ≠ 某个词掉榜：所有词都凑不满 7 期，榜单据实为空。"""
    for i in range(3):
        ranks = _decay(60000 + i * 1000, _HIT_RATES)
        del ranks[WINDOW[2]]  # 这一期整体缺失，每个词都少一天
        _seed(session, f"hit-{i}", ranks)

    assert load_growth(session, SITE, ANCHOR) == 0


def test_同期重算幂等(session):
    _seed(session, "hit", _decay(60000, _HIT_RATES))

    first = load_growth(session, SITE, ANCHOR)
    second = load_growth(session, SITE, ANCHOR)

    assert first == second == 1
    # 覆盖语义：先删该切片再重算，不是追加。重复算定不该让同一个词出现两次。
    total = session.execute(
        text("SELECT count(*) FROM analysis.fact_growth WHERE site=:s AND anchor_date=:d"),
        {"s": SITE, "d": ANCHOR},
    ).scalar()
    assert total == 1


def test_只算本站点(session):
    """另一个站点的同名词不该被卷进来——站点是硬边界。"""
    ddl_engine = create_engine(TEST_URL)
    try:
        with ddl_engine.begin() as ddl:
            ensure_fact_partitions(ddl, "fact_daily", "DE", ANCHOR)
            ensure_fact_partitions(ddl, "fact_daily", "DE", WINDOW[0])
    finally:
        ddl_engine.dispose()  # 共享测试库，别把连接留给别的会话去等

    kid = session.execute(
        text("INSERT INTO analysis.dim_keyword(site,keyword) VALUES ('DE','hit') "
             "RETURNING keyword_id"),
    ).scalar()
    for d, rank in _decay(60000, _HIT_RATES).items():
        session.execute(
            text("INSERT INTO analysis.fact_daily(site,report_date,keyword_id,current_ranking) "
                 "VALUES ('DE',:d,:k,:r)"),
            {"d": d, "k": kid, "r": rank},
        )

    assert load_growth(session, SITE, ANCHOR) == 0
