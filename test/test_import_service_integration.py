"""导入编排层的集成测试：批次记录、统计、物化视图刷新、原始文件留存、串行锁。

OSS-first 之后不再有导入后本地归档（文件入口即持久化于 OSS），故这里断言的是
"原始文件留在原处、不产生归档目录"。

与 importer 的测试不同，本层会 **提交事务**（真实写库），因此不能用回滚隔离。
每个测试用后 TRUNCATE 清理。只在 TEST_DATABASE_URL 时运行。

运行::

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_import_service_integration.py -v
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="设置 TEST_DATABASE_URL 运行编排层集成测试")

_META = "报告范围=US"
_HEADER = ",".join(["搜索频率排名", "搜索词"] + [f"col{i}" for i in range(19)])


def _row(rank, keyword, a1="B1", t1="T", b1="Brand", cat1="Electronics", cs1="10.0"):
    cells = [str(rank), keyword, b1, "", "", cat1, "", "", a1, t1, cs1, "",
             "", "", "", "", "", "", "", "", "2026-07-15"]
    buf = io.StringIO(); csv.writer(buf).writerow(cells)
    return buf.getvalue().rstrip("\r\n")


def _write_csv(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join([_META, _HEADER, *rows]) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture()
def factory():
    engine = create_engine(TEST_URL)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    yield sf
    # 清理：TRUNCATE 全部业务表 + 物化视图数据
    with engine.begin() as c:
        c.execute(text("TRUNCATE analysis.fact_daily, analysis.fact_weekly, "
                       "analysis.fact_dropped, "
                       "analysis.dim_keyword, analysis.dim_product, "
                       "analysis.site_data_stats, analysis.import_batch RESTART IDENTITY CASCADE"))
    engine.dispose()


def test_full_import_from_local_source_records_batch_and_stats(factory, tmp_path):
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day_2026_07_15.csv", [_row(1, "a"), _row(2, "b")])
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(path), original_filename="US_Day_2026_07_15.csv",
                       session_factory=factory)
    assert batch.status == "COMPLETED"
    assert batch.written_rows == 2

    with factory() as s:
        # 事实落库
        # 计数**必须带作用域**：keywordiq_test 是全局共享的一份，别的会话或一次
        # verify-import 留下的已提交数据会让裸 count(*) 数到不属于本测试的行。
        assert s.execute(text("SELECT count(*) FROM analysis.fact_daily "
                              "WHERE site='US' AND report_date='2026-07-15'")).scalar() == 2
        # 统计写入
        n = s.execute(text("SELECT row_count FROM analysis.site_data_stats "
                           "WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'")).scalar()
        assert n == 2
        # 批次记录可查
        assert s.execute(text("SELECT count(*) FROM analysis.import_batch "
                              "WHERE status='COMPLETED' AND site='US' "
                              "AND report_date='2026-07-15'")).scalar() == 1
        # 物化视图已刷新（有类目数据）
        cats = s.execute(text("SELECT count(*) FROM analysis.category_stats WHERE site='US'")).scalar()
        assert cats >= 1

    # OSS-first：不再做导入后本地归档，原始文件留在原处；本地来源无 object_key。
    assert Path(path).exists()
    assert not (tmp_path / "archive").exists()


def test_failed_import_preserves_file_and_marks_failed(factory, tmp_path):
    from app.table.upload.import_service import run_import

    # 结构不符（缺元数据行的关键字），触发失败
    from app.table.upload.import_source import LocalFileSource
    bad = tmp_path / "bad.csv"
    bad.write_text("not,a,valid,report\n", encoding="utf-8")
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(str(bad)), original_filename="bad.csv",
                       session_factory=factory)
    # 结构不符 → 失败，原始文件保留（不归档）供排查
    assert batch.status == "FAILED"
    assert batch.completed_at is not None  # ops-bot 失败通知按它取
    assert batch.error_message
    assert Path(bad).exists()


@pytest.fixture()
def lock_holder():
    """占住导入串行锁，模拟「已有导入在跑」。"""
    engine = create_engine(TEST_URL)
    holder = engine.connect()
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY})
    holder.commit()

    def release():
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
        holder.commit()

    try:
        yield release
    finally:
        release()
        holder.close()
        engine.dispose()


@pytest.fixture()
def ops_action_lock_holder():
    """占住运维动作锁，模拟 ops-bot 正在执行重启/发布（PR #99 Codex 复审 P1 回归）。"""
    engine = create_engine(TEST_URL)
    holder = engine.connect()
    from app.table.upload.import_service import _OPS_ACTION_LOCK_KEY
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _OPS_ACTION_LOCK_KEY})
    holder.commit()

    def release():
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _OPS_ACTION_LOCK_KEY})
        holder.commit()

    try:
        yield release
    finally:
        release()
        holder.close()
        engine.dispose()


def test_import_does_not_start_processing_while_ops_action_holds_the_lock(
    factory, tmp_path, ops_action_lock_holder
):
    """运维动作锁被占着时，导入不能转入 PROCESSING——这是导入与重启/发布真正的
    互斥边界（`_claim_processing_slot`），不是"探测到空闲就直接写"那个有竞态窗口
    的旧实现（PR #99 Codex 复审 P1：探测后立即放锁，导入侧与 ops-bot 侧可能并发
    改写状态）。有 object_key 的批次转入 QUEUED 等待重试，不能假装导入正在跑。
    """
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
    batch = run_import(
        site="US", period="DAY", report_date=date(2026, 7, 15),
        source=LocalFileSource(path), original_filename="US_Day.csv",
        object_key="imports/US/DAY/2026-07-15/abc_US_Day.csv",
        session_factory=factory,
    )
    assert batch.status == "QUEUED"
    with factory() as s:
        assert s.execute(text("SELECT count(*) FROM analysis.fact_daily")).scalar() == 0

    ops_action_lock_holder()  # 运维动作结束，锁释放

    from app.table.upload.import_service import drain_import_queue
    drained = drain_import_queue(source_factory=lambda k: LocalFileSource(path), session_factory=factory)
    assert drained is not None
    assert drained.status == "COMPLETED"


def test_local_fallback_fails_fast_when_ops_action_holds_the_lock(
    factory, tmp_path, ops_action_lock_holder
):
    """本地退路没有 object_key，运维动作锁被占着时同样不能留在 QUEUED 假装排队——
    与"另一个导入持有 IMPORT_LOCK_KEY"时的既有纪律一致（见
    test_rejected_local_fallback_fails_fast_instead_of_queueing_forever）。
    """
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
    batch = run_import(
        site="US", period="DAY", report_date=date(2026, 7, 15),
        source=LocalFileSource(path), original_filename="US_Day.csv",
        session_factory=factory,
    )
    assert batch.status == "FAILED"
    assert batch.completed_at is not None  # ops-bot 失败通知按它取
    assert "重新上传" in batch.error_message


def test_second_concurrent_import_is_queued(factory, tmp_path, lock_holder):
    """持有锁时，可重排的批次（有 object_key）留在 QUEUED 等 drain 接手。"""
    from app.table.upload.import_service import ImportRejected, run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
    with pytest.raises(ImportRejected):
        run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                   source=LocalFileSource(path), original_filename="US_Day.csv",
                   object_key="imports/US/DAY/2026-07-15/abc_US_Day.csv",
                   session_factory=factory)
    with factory() as s:
        assert s.execute(text("SELECT status FROM analysis.import_batch")).scalar() == "QUEUED"


def test_rejected_local_fallback_fails_fast_instead_of_queueing_forever(
    factory, tmp_path, lock_holder
):
    """本地退路没有 object_key，重建不出源——不能留在 QUEUED 假装在排队。

    这是历史上那个洞：批次停在 QUEUED，界面显示「队列中」，而没有任何东西会回头
    看它。用户在等一件永远不会发生的事，且没有任何提示告诉他需要重传。
    """
    from app.table.upload.import_service import ImportRejected, run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
    with pytest.raises(ImportRejected):
        run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                   source=LocalFileSource(path), original_filename="US_Day.csv",
                   session_factory=factory)
    with factory() as s:
        status, err = s.execute(
            text("SELECT status, error_message FROM analysis.import_batch")
        ).one()
    assert status == "FAILED"
    assert "重新上传" in err


def test_drain_runs_the_queued_batch_once_the_lock_frees(factory, tmp_path, lock_holder):
    """锁释放后，drain 接手排队批次并真正导完。"""
    from app.table.upload.import_service import ImportRejected, drain_import_queue, run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a"), _row(2, "b")])
    key = "imports/US/DAY/2026-07-15/abc_US_Day.csv"
    with pytest.raises(ImportRejected):
        run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                   source=LocalFileSource(path), original_filename="US_Day.csv",
                   object_key=key, session_factory=factory)

    # 锁还占着：drain 不该动它（串行约束高于「尽快跑完」）
    assert drain_import_queue(
        source_factory=lambda k: LocalFileSource(path), session_factory=factory
    ) is None
    with factory() as s:
        assert s.execute(text("SELECT status FROM analysis.import_batch")).scalar() == "QUEUED"

    lock_holder()  # 前一个导入结束

    drained = drain_import_queue(
        source_factory=lambda k: LocalFileSource(path), session_factory=factory
    )
    assert drained is not None
    assert drained.status == "COMPLETED"
    assert drained.written_rows == 2
    with factory() as s:
        # 复用的是原来那条批次记录，不是新建一条——否则界面会留下一条永不消失的 QUEUED
        assert s.execute(text("SELECT count(*) FROM analysis.import_batch")).scalar() == 1
        assert s.execute(
            text("SELECT count(*) FROM analysis.fact_daily WHERE site='US' "
                 "AND report_date='2026-07-15'")
        ).scalar() == 2
        # 导入后统计回写照样发生（drain 走的是同一条执行路径）
        assert s.execute(
            text("SELECT row_count FROM analysis.site_data_stats WHERE site='US' "
                 "AND period_type='DAY' AND report_date='2026-07-15'")
        ).scalar() == 2


def _lock_is_free() -> bool:
    """从**独立连接**看这把锁是不是空的。

    必须换一条连接来看：咨询锁是会话级的，同一条连接再抢自己已经持有的锁会直接成功
    （可重入，只是把计数加一）。用导入自己那条连接去验，永远是绿的——这正是这个泄漏
    能一直藏着的原因。
    """
    engine = create_engine(TEST_URL)
    try:
        with engine.connect() as c:
            from app.table.upload.import_service import IMPORT_LOCK_KEY
            got = c.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY}
            ).scalar()
            if got:
                c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
            return bool(got)
    finally:
        engine.dispose()


def test_import_lock_survives_session_connection_churn():
    """锁必须绑在专属连接上，不能跟着 Session 漂。

    咨询锁是**会话（连接）级**的，而 Session 每次 commit 都会把连接还给池子；池子里
    只要有别的活动（健康检查、导出、另一次查询），下一条语句就可能换到另一条连接上。
    此后的 `pg_advisory_unlock` 跑在错的连接上，静默返回 false 什么也没解开，锁就此
    挂在池里那条空闲连接上直到进程退出。

    实测确认过这条路径：取锁 pid 56739 → commit → 池子被两个会话用过 → pid 56741，
    unlock 返回 False。后续导入还能跑，只是因为「同一条连接再抢自己持有的锁会成功」
    （可重入）——直到某次拿到别的连接为止。
    """
    from app.table.upload.import_service import import_lock
    # 池子要放得下 4 条：锁一条（专属）、导入会话一条、制造漂移的两条
    engine = create_engine(TEST_URL, pool_size=4, max_overflow=0)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    session = sf()
    try:
        with import_lock(session) as got:
            assert got
            # 制造连接漂移：会话还回连接，别人先拿走，会话下次拿到的就是另一条
            session.execute(text("SELECT 1")); session.commit()
            others = [sf() for _ in range(2)]
            for o in others:
                o.execute(text("SELECT 1"))
            session.execute(text("SELECT 1")); session.commit()
            for o in others:
                o.close()
            # 漂移之后锁仍然被真正持有：完全独立的连接抢不到
            assert not _lock_is_free(), "持锁期间别人竟然抢到了锁——串行约束已失效"
        assert _lock_is_free(), "出了 with 之后锁没还回去"
    finally:
        session.close()
        engine.dispose()


def test_lock_is_released_after_import_completes(factory, tmp_path):
    """导入结束后锁必须真的还回去。

    会话级咨询锁绑在**连接**上，而 Session 每次 commit 都会把连接还给池子——之后的
    `pg_advisory_unlock` 可能跑在另一条连接上，那是个静默无效的调用（返回 false），
    锁就此挂在池里那条空闲连接上直到进程退出。表现是「导入全部完成、库里没有任何活动，
    锁却还被占着」，而后续导入靠「同一条连接可重入」侥幸继续工作，直到某次拿到别的
    连接为止。
    """
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    path = _write_csv(tmp_path, "US_Day.csv", [_row(1, "a")])
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource(path), original_filename="US_Day.csv",
                       session_factory=factory)
    assert batch.status == "COMPLETED"
    assert _lock_is_free(), "导入完成后咨询锁仍被占着——后续导入会被永久判为「已有导入在进行」"


def test_lock_is_released_after_import_fails(factory, tmp_path):
    """失败路径同样要还锁——失败比成功更容易把锁漏在那儿。"""
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                       source=LocalFileSource("/nonexistent/nope.csv"),
                       original_filename="nope.csv", session_factory=factory)
    assert batch.status == "FAILED"
    assert batch.completed_at is not None  # ops-bot 失败通知按它取
    assert _lock_is_free(), "导入失败后咨询锁仍被占着"


def test_drain_returns_none_when_queue_is_empty(factory, tmp_path):
    from app.table.upload.import_service import drain_import_queue
    from app.table.upload.import_source import LocalFileSource
    assert drain_import_queue(
        source_factory=lambda k: LocalFileSource("/nonexistent"), session_factory=factory
    ) is None



# --------------------------------------------------------------------------
# 掉榜集合：导入收尾时算定
# --------------------------------------------------------------------------


def _import_day(factory, tmp_path, day: str, rows) -> None:
    """导入某一天的日报。``day`` 形如 ``2026-07-15``。"""
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    name = f"US_Day_{day.replace('-', '_')}.csv"
    path = _write_csv(tmp_path, name, rows)
    run_import(site="US", period="DAY", report_date=date.fromisoformat(day),
               source=LocalFileSource(path), original_filename=name, session_factory=factory)


def _dropped(factory, anchor: str) -> dict[str, tuple[str, int]]:
    """取某锚定期的掉榜记录：关键词 -> (最后在榜期, 最后排名)。"""
    with factory() as s:
        rows = s.execute(text(
            "SELECT k.keyword, d.last_seen_date, d.last_ranking "
            "  FROM analysis.fact_dropped d "
            "  JOIN analysis.dim_keyword k ON k.keyword_id = d.keyword_id "
            " WHERE d.site='US' AND d.period_type='DAY' AND d.anchor_date=:a"
        ), {"a": anchor}).all()
    return {kw: (str(seen), rank) for kw, seen, rank in rows}


def test_dropped_set_carries_last_seen_period_and_ranking(factory, tmp_path):
    """窗口内掉出的词进掉榜表，并带上它最后一次在榜的期次与名次。

    这两个数是掉榜行唯一能携带的跨期信息——界面上那一行除了历史排名与趋势，全靠它们。
    """
    _import_day(factory, tmp_path, "2026-07-14", [_row(1, "a"), _row(2, "b"), _row(3, "c")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a"), _row(2, "b")])

    assert _dropped(factory, "2026-07-15") == {"c": ("2026-07-14", 3)}


def test_listed_keywords_are_not_in_dropped_set(factory, tmp_path):
    """当期在榜的词不进掉榜表——两段行集合必须互斥，否则分页会把同一个词数两遍。"""
    _import_day(factory, tmp_path, "2026-07-14", [_row(1, "a"), _row(2, "b")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a"), _row(2, "b")])

    assert _dropped(factory, "2026-07-15") == {}


def test_last_seen_takes_most_recent_period_in_window(factory, tmp_path):
    """一个词在窗口内出现多次时，取**最近**那一期，不是最早那一期。"""
    _import_day(factory, tmp_path, "2026-07-13", [_row(1, "a"), _row(9, "c")])
    _import_day(factory, tmp_path, "2026-07-14", [_row(1, "a"), _row(5, "c")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a")])

    assert _dropped(factory, "2026-07-15") == {"c": ("2026-07-14", 5)}


def test_keyword_dropped_before_window_is_excluded(factory, tmp_path):
    """行窗口是锚定日往前 7 天；更早掉出的词不该出现。

    没有这条边界，掉榜段会随保留窗口一起膨胀——三个月前掉的词也会挤进今天这张表。
    """
    _import_day(factory, tmp_path, "2026-07-05", [_row(1, "a"), _row(7, "old")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a")])

    assert "old" not in _dropped(factory, "2026-07-15")


def test_reimporting_same_period_does_not_duplicate_dropped_rows(factory, tmp_path):
    """同一期重复导入先删该期再重算，与事实数据的覆盖语义一致。"""
    _import_day(factory, tmp_path, "2026-07-14", [_row(1, "a"), _row(3, "c")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a")])

    assert _dropped(factory, "2026-07-15") == {"c": ("2026-07-14", 3)}


def test_dropped_count_recorded_in_site_data_stats(factory, tmp_path):
    """掉榜数与在榜数同粒度预存，分页总数才能保持 O(1)。"""
    _import_day(factory, tmp_path, "2026-07-14", [_row(1, "a"), _row(2, "b"), _row(3, "c")])
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a")])

    with factory() as s:
        listed, dropped = s.execute(text(
            "SELECT row_count, dropped_count FROM analysis.site_data_stats "
            " WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'"
        )).one()
    assert (listed, dropped) == (1, 2)


# ---------------- 重导（导入历史屏） ----------------


def test_retry_creates_a_new_batch_and_leaves_the_failed_one_intact(factory, tmp_path):
    """重导新建批次记录，**不改写**被重导的那条。

    原记录的状态与错误信息是「这次为什么失败」的唯一留存，也是判断「重导后是否真的
    修好了」的参照。改写它等于把证据抹掉，历史屏上就只剩一条看不出曾经失败过的成功记录。

    重导没有独立的执行路径——它就是拿同一份归属再调一次 ``run_import``，而后者在拿锁
    之前先落一条新批次。这个测试同时钉住那个前提。
    """
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource

    key = "imports/US/DAY/2026-07-15/ab12_US_Day_2026_07_15.csv"

    bad = tmp_path / "US_Day_2026_07_15.csv"
    bad.write_text("not,a,valid,report\n", encoding="utf-8")
    failed = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                        source=LocalFileSource(str(bad)),
                        original_filename="US_Day_2026_07_15.csv",
                        object_key=key, session_factory=factory)
    assert failed.status == "FAILED"
    failed_id, failed_error = failed.id, failed.error_message

    # 重导：同一归属、同一 object key，这次源是好的
    good = _write_csv(tmp_path, "US_Day_2026_07_15.csv", [_row(1, "a"), _row(2, "b")])
    retried = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                         source=LocalFileSource(good),
                         original_filename="US_Day_2026_07_15.csv",
                         object_key=key, session_factory=factory)

    assert retried.status == "COMPLETED"
    assert retried.id != failed_id

    with factory() as s:
        # 原记录原封不动
        status, error = s.execute(text(
            "SELECT status, error_message FROM analysis.import_batch WHERE id=:i"
        ), {"i": failed_id}).one()
        assert status == "FAILED"
        assert error == failed_error

        # 同一份文件的两次执行都在历史里（计数带作用域：keywordiq_test 是共享的一份）
        n = s.execute(text(
            "SELECT count(*) FROM analysis.import_batch "
            " WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'"
        )).scalar()
        assert n == 2

        # 数据确实被重导进来了
        assert s.execute(text(
            "SELECT count(*) FROM analysis.fact_daily "
            " WHERE site='US' AND report_date='2026-07-15'"
        )).scalar() == 2


# ---------------------------------------------------------------------------
# 导入后查询统计（#148，openspec/changes/post-import-query-readiness）
#
# 新期次不在叶子分区统计里时规划器把整期估成 1 行，查询退化到十几秒乃至超时。导入必须在
# 期次可见（写 site_data_stats）之前 ANALYZE 写入的叶子分区；失败不回滚、可见、可重试。
# ---------------------------------------------------------------------------

def _stats_mention(session, leaf: str, column: str, value: str) -> bool:
    """叶子分区的统计（MCV 或直方图）里是否已出现该日期——即规划器是否「认识」这一期。"""
    row = session.execute(text(
        "SELECT coalesce(most_common_vals::text, '') || coalesce(histogram_bounds::text, '') "
        "  FROM pg_stats WHERE schemaname='analysis' AND tablename=:t AND attname=:c"
    ), {"t": leaf, "c": column}).scalar()
    return bool(row) and value in row


def _batch_stats(factory, batch_id):
    with factory() as s:
        return s.execute(text(
            "SELECT status, stats_status, stats_attempts FROM analysis.import_batch WHERE id=:i"
        ), {"i": batch_id}).one()


def _three_rows():
    # 每期多行同一日期，ANALYZE 才会把它记进 MCV
    return [_row(1, "a"), _row(2, "b"), _row(3, "c")]


def test_query_stats_cover_new_period_before_it_becomes_visible(factory, tmp_path, monkeypatch):
    """第二天导入进**已有统计**的同月分区：写 site_data_stats 那一刻，统计里必须已有这一天。

    这正是 #148 实测最慢的形态（统计存在但不含新期，估 1 行、实际 37 万行）。
    """
    import app.table.upload.import_service as svc

    _import_day(factory, tmp_path, "2026-07-15", _three_rows())

    seen_at_visibility: list[bool] = []
    original = svc._update_site_data_stats

    def spy(session, **kw):
        if kw["report_date"] == date(2026, 7, 16):
            seen_at_visibility.append(
                _stats_mention(session, "fact_daily_2026_07_us", "report_date", "2026-07-16")
            )
        return original(session, **kw)

    monkeypatch.setattr(svc, "_update_site_data_stats", spy)
    _import_day(factory, tmp_path, "2026-07-16", _three_rows())

    assert seen_at_visibility == [True]
    with factory() as s:
        status, stats_status = s.execute(text(
            "SELECT status, stats_status FROM analysis.import_batch "
            " WHERE site='US' AND report_date='2026-07-16'"
        )).one()
    assert (status, stats_status) == ("COMPLETED", "READY")


def test_query_stats_refresh_covers_written_leaves_for_week_and_reimport(factory, tmp_path):
    """周报分析周报叶子与掉榜分区（没有增长分区）；同一期重导同样刷新。"""
    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource
    from app.table.upload.importer import written_leaf_partitions

    assert written_leaf_partitions("US", "WEEK", date(2026, 7, 12)) == [
        "fact_weekly_2026_07_us", "fact_dropped_2026_07",
    ]
    assert written_leaf_partitions("gb", "DAY", date(2026, 7, 12)) == [
        "fact_daily_2026_07_gb", "fact_dropped_2026_07", "fact_growth_2026_07",
    ]

    for _ in range(2):  # 第二次是同一期重导
        path = _write_csv(tmp_path, "US_Week_2026_07_12.csv", _three_rows())
        batch = run_import(site="US", period="WEEK", report_date=date(2026, 7, 12),
                           source=LocalFileSource(path), original_filename="US_Week_2026_07_12.csv",
                           session_factory=factory)
        assert (batch.status, batch.stats_status) == ("COMPLETED", "READY")

    with factory() as s:
        assert _stats_mention(s, "fact_weekly_2026_07_us", "report_date_week", "2026-07-12")
        analyzed = s.execute(text(
            "SELECT relname FROM pg_stat_user_tables "
            " WHERE schemaname='analysis' AND last_analyze IS NOT NULL "
            "   AND relname IN ('fact_weekly_2026_07_us', 'fact_dropped_2026_07')"
        )).scalars().all()
    assert sorted(analyzed) == ["fact_dropped_2026_07", "fact_weekly_2026_07_us"]


def test_query_stats_failure_keeps_data_visible_and_pending(factory, tmp_path, monkeypatch):
    """刷新失败不回滚、不隐藏：批次照常完成、期次可选，只是查询统计待更新。"""
    import app.table.upload.import_service as svc

    def boom(*_a, **_k):
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr(svc, "refresh_query_stats", boom)
    path = _write_csv(tmp_path, "US_Day_2026_07_15.csv", _three_rows())
    from app.table.upload.import_source import LocalFileSource
    batch = svc.run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                           source=LocalFileSource(path), original_filename="US_Day_2026_07_15.csv",
                           session_factory=factory)

    assert _batch_stats(factory, batch.id) == ("COMPLETED", "PENDING", 1)
    with factory() as s:
        assert s.execute(text(
            "SELECT row_count FROM analysis.site_data_stats "
            " WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'"
        )).scalar() == 3


def test_retry_turns_pending_into_ready_when_idle(factory, tmp_path, monkeypatch):
    import app.table.upload.import_service as svc

    real = svc.refresh_query_stats
    monkeypatch.setattr(svc, "refresh_query_stats", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _import_day(factory, tmp_path, "2026-07-15", _three_rows())
    monkeypatch.setattr(svc, "refresh_query_stats", real)

    assert svc.retry_pending_query_stats(session_factory=factory) == 1
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
        assert _stats_mention(s, "fact_daily_2026_07_us", "report_date", "2026-07-15")
    assert _batch_stats(factory, batch_id) == ("COMPLETED", "READY", 1)
    # 没有待更新的了：下一轮什么也不做
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_retry_gives_up_after_max_attempts(factory, tmp_path, monkeypatch):
    import app.table.upload.import_service as svc
    from app.table.upload.import_batch_model import STATS_MAX_ATTEMPTS

    monkeypatch.setattr(svc, "refresh_query_stats", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _import_day(factory, tmp_path, "2026-07-15", _three_rows())  # 第 1 次失败

    for _ in range(STATS_MAX_ATTEMPTS - 1):
        assert svc.retry_pending_query_stats(session_factory=factory) == 1
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("COMPLETED", "FAILED", STATS_MAX_ATTEMPTS)
    # 用尽后不再重试
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_retry_skips_while_an_import_holds_the_lock(factory, tmp_path, monkeypatch):
    import app.table.upload.import_service as svc

    monkeypatch.setattr(svc, "refresh_query_stats", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _import_day(factory, tmp_path, "2026-07-15", _three_rows())

    engine = create_engine(TEST_URL)
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY})
        try:
            assert svc.retry_pending_query_stats(session_factory=factory) == 0
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    engine.dispose()
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("COMPLETED", "PENDING", 1)


def test_import_failing_after_facts_commit_has_no_query_stats_status(factory, tmp_path, monkeypatch):
    """事实已提交（PENDING 随之落库）但收尾失败：批次判失败，查询统计「不适用」，
    否则界面会在一个失败批次上提示「统计待更新」，重试任务也会去碰它。"""
    import app.table.upload.import_service as svc

    def boom(*_a, **_k):
        raise RuntimeError("site_data_stats 写入失败")

    monkeypatch.setattr(svc, "_update_site_data_stats", boom)
    path = _write_csv(tmp_path, "US_Day_2026_07_15.csv", _three_rows())
    from app.table.upload.import_source import LocalFileSource
    batch = svc.run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                           source=LocalFileSource(path), original_filename="US_Day_2026_07_15.csv",
                           session_factory=factory)
    assert _batch_stats(factory, batch.id) == ("FAILED", None, 0)


def _crash(*_a, **_k):
    # 进程退出不是 Exception：导入的 except 分支拦不住，批次就停在中途的状态
    raise SystemExit("worker killed")


@pytest.mark.parametrize(
    ("crash_point", "stats_at_crash"),
    [("refresh_query_stats", "PENDING"), ("_update_site_data_stats", "READY")],
)
def test_retry_finishes_import_interrupted_after_facts_commit(
    factory, tmp_path, monkeypatch, crash_point, stats_at_crash
):
    """事实已提交、收尾中途进程退出：批次停在 PROCESSING，这一期不可见。空闲时的恢复任务
    要把收尾补完——统计、site_data_stats（含掉榜数）、完成状态（PR #149 Codex 复审 P2）。"""
    import app.table.upload.import_service as svc

    _import_day(factory, tmp_path, "2026-07-15", _three_rows())
    with monkeypatch.context() as m:
        m.setattr(svc, crash_point, _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-16", [_row(1, "a"), _row(2, "b")])

    with factory() as s:
        batch_id = s.execute(text(
            "SELECT id FROM analysis.import_batch WHERE report_date='2026-07-16'"
        )).scalar()
        assert s.execute(text(
            "SELECT count(*) FROM analysis.site_data_stats WHERE report_date='2026-07-16'"
        )).scalar() == 0
    assert _batch_stats(factory, batch_id) == ("PROCESSING", stats_at_crash, 0)

    assert svc.retry_pending_query_stats(session_factory=factory) == 1

    assert _batch_stats(factory, batch_id) == ("COMPLETED", "READY", 0)
    with factory() as s:
        assert _stats_mention(s, "fact_daily_2026_07_us", "report_date", "2026-07-16")
        assert s.execute(text(
            "SELECT row_count, dropped_count FROM analysis.site_data_stats "
            " WHERE site='US' AND period_type='DAY' AND report_date='2026-07-16'"
        )).one() == (2, 1)
        assert s.execute(text(
            "SELECT written_rows, completed_at IS NOT NULL FROM analysis.import_batch WHERE id=:i"
        ), {"i": batch_id}).one() == (2, True)
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_interrupted_finish_that_fails_again_is_marked_failed(factory, tmp_path, monkeypatch):
    """补完收尾再失败，与正常导入收尾失败同样判失败——不无限重试、不留「统计待更新」。"""
    import app.table.upload.import_service as svc

    with monkeypatch.context() as m:
        m.setattr(svc, "refresh_query_stats", _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-15", _three_rows())

    def boom(*_a, **_k):
        raise RuntimeError("site_data_stats 写入失败")

    monkeypatch.setattr(svc, "_update_site_data_stats", boom)
    assert svc.retry_pending_query_stats(session_factory=factory) == 1
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("FAILED", None, 0)
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_import_interrupted_before_facts_commit_is_marked_failed(factory, tmp_path, monkeypatch):
    """退在事实事务里（stats_status 为空）：没有可补的收尾，判失败——否则记录永远「处理中」、
    不能重导，ops-bot 也一直以为有导入在跑。不发布期次、不动任何数据。"""
    import app.table.upload.import_service as svc

    with monkeypatch.context() as m:
        m.setattr(svc, "import_report", _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-15", _three_rows())
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("PROCESSING", None, 0)

    assert svc.retry_pending_query_stats(session_factory=factory) == 1
    assert _batch_stats(factory, batch_id) == ("FAILED", None, 0)
    with factory() as s:
        assert "导入进程中断" in s.execute(text(
            "SELECT error_message FROM analysis.import_batch WHERE id=:i"
        ), {"i": batch_id}).scalar()
        assert s.execute(text(
            "SELECT count(*) FROM analysis.site_data_stats WHERE report_date='2026-07-15'"
        )).scalar() == 0
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_processing_batch_of_a_running_import_is_not_touched(factory, tmp_path, monkeypatch):
    """导入正持锁时 PROCESSING 是真的在跑：恢复任务拿不到锁，跳过本轮，不碰它。"""
    import app.table.upload.import_service as svc

    with monkeypatch.context() as m:
        m.setattr(svc, "import_report", _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-15", _three_rows())

    engine = create_engine(TEST_URL)
    from app.table.upload.import_service import IMPORT_LOCK_KEY
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": IMPORT_LOCK_KEY})
        try:
            assert svc.retry_pending_query_stats(session_factory=factory) == 0
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": IMPORT_LOCK_KEY})
    engine.dispose()
    with factory() as s:
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("PROCESSING", None, 0)


def test_recovery_uses_current_slice_after_the_period_was_reimported(factory, tmp_path, monkeypatch):
    """收尾中断后同一期先被重导（排队接手排在恢复之前）：恢复不能拿旧批次的行数覆盖
    新导入写好的 site_data_stats，否则分页总数与在榜/掉榜分界错位（PR #149 Codex 复审）。"""
    import app.table.upload.import_service as svc

    with monkeypatch.context() as m:
        m.setattr(svc, "refresh_query_stats", _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-15", _three_rows())
    _import_day(factory, tmp_path, "2026-07-15", [_row(1, "a"), _row(2, "b")])

    assert svc.retry_pending_query_stats(session_factory=factory) == 1
    with factory() as s:
        assert s.execute(text(
            "SELECT row_count FROM analysis.site_data_stats "
            " WHERE site='US' AND period_type='DAY' AND report_date='2026-07-15'"
        )).scalar() == 2
        statuses = s.execute(text(
            "SELECT status FROM analysis.import_batch ORDER BY id"
        )).scalars().all()
    assert statuses == ["COMPLETED", "COMPLETED"]


def test_recovery_does_not_republish_a_period_whose_facts_are_gone(factory, tmp_path, monkeypatch):
    """收尾中断后保留清理先移除了这一期：恢复不能凭批次上记的行数重新发布它
    （那会让已删掉的期次带着正数总数出现在日期选择器里，PR #149 Codex 复审）。"""
    import app.table.upload.import_service as svc

    with monkeypatch.context() as m:
        m.setattr(svc, "_update_site_data_stats", _crash)
        with pytest.raises(SystemExit):
            _import_day(factory, tmp_path, "2026-07-15", _three_rows())
    with factory() as s:  # 模拟保留清理已移除该期的事实与掉榜
        s.execute(text("DELETE FROM analysis.fact_daily WHERE site='US' AND report_date='2026-07-15'"))
        s.execute(text("DELETE FROM analysis.fact_dropped WHERE site='US' AND anchor_date='2026-07-15'"))
        s.commit()
        batch_id = s.execute(text("SELECT id FROM analysis.import_batch")).scalar()
    assert _batch_stats(factory, batch_id) == ("PROCESSING", "READY", 0)

    assert svc.retry_pending_query_stats(session_factory=factory) == 1
    assert _batch_stats(factory, batch_id) == ("FAILED", None, 0)
    with factory() as s:
        assert s.execute(text(
            "SELECT count(*) FROM analysis.site_data_stats WHERE report_date='2026-07-15'"
        )).scalar() == 0
    assert svc.retry_pending_query_stats(session_factory=factory) == 0


def test_completed_at_is_true_instant_when_process_tz_differs_from_session_tz(tmp_path, monkeypatch):
    """生产形态：容器本地时区 UTC、引擎会话时区 Asia/Shanghai（database.py）。裸 datetime.now()
    的 UTC 墙钟会被会话当成北京时间写入，完成时间比真实时刻早 8 小时，ops-bot 的导入通知
    窗口永远扫不到它。"""
    import time

    from app.table.upload.import_service import run_import
    from app.table.upload.import_source import LocalFileSource

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    engine = create_engine(TEST_URL, connect_args={"options": "-c timezone=Asia/Shanghai"})
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        path = _write_csv(tmp_path, "US_Day_2026_07_15.csv", [_row(1, "a")])
        batch = run_import(site="US", period="DAY", report_date=date(2026, 7, 15),
                           source=LocalFileSource(path), original_filename="US_Day_2026_07_15.csv",
                           session_factory=sf)
        assert batch.status == "COMPLETED"
        with sf() as s:
            lag = s.execute(text(
                "SELECT extract(epoch FROM now() - completed_at), "
                "       extract(epoch FROM completed_at - created_at) "
                "  FROM analysis.import_batch WHERE id = :i"
            ), {"i": batch.id}).one()
        assert 0 <= lag[0] < 60
        assert lag[1] >= 0
    finally:
        with engine.begin() as c:
            c.execute(text("TRUNCATE analysis.fact_daily, analysis.fact_weekly, analysis.fact_dropped, "
                           "analysis.dim_keyword, analysis.dim_product, analysis.site_data_stats, "
                           "analysis.import_batch RESTART IDENTITY CASCADE"))
        engine.dispose()
        monkeypatch.delenv("TZ")
        time.tzset()
