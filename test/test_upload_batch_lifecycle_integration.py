"""直传批次生命周期的数据库集成测试——真 PostgreSQL，桩掉 OSS。

单元测试用替身会话验证了分支逻辑，这里验证的是**真库上的那几件事**：
`UPLOADING` 这个值真能写进 `String(16)` 的列（少一个字符就会截断或报错）、按 object key
的认领真能查到、回收的时间窗口真按 `created_at`（带时区）比较。

只在设置了 `TEST_DATABASE_URL` 时运行，且必须是一次性测试库。

运行::

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_upload_batch_lifecycle_integration.py -v
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.table.upload import import_service
from app.table.upload.import_batch_model import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_UPLOADING,
    ImportBatch,
)

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)

SITE = "US"
PERIOD = "DAY"
RDATE = date(2026, 7, 18)
KEY = "imports/US/DAY/2026-07-18/abc123_US_x_Day_2026_07_18.csv"


@pytest.fixture()
def factory():
    engine = create_engine(TEST_URL)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    yield sf
    # 只清本用例写的那几条：裸 TRUNCATE 会连带清掉别的会话留在库里的数据，
    # 而这个库是共享的（见 CLAUDE.md「共享测试库」）。
    with engine.begin() as c:
        c.execute(text("DELETE FROM analysis.import_batch WHERE object_key LIKE :k"),
                  {"k": "imports/US/DAY/2026-07-18/%"})
    engine.dispose()


def _make_uploading(factory, *, age: timedelta, key: str = KEY) -> int:
    """直接落一条指定年龄的 UPLOADING 批次，返回 id。"""
    session = factory()
    try:
        batch = ImportBatch(
            site=SITE, period_type=PERIOD, report_date=RDATE,
            file_name="US_x_Day_2026_07_18.csv", object_key=key,
            status=STATUS_UPLOADING,
            created_at=datetime.now(UTC) - age,
        )
        session.add(batch)
        session.commit()
        return batch.id
    finally:
        session.close()


def _status(factory, batch_id: int) -> str:
    session = factory()
    try:
        return session.get(ImportBatch, batch_id).status
    finally:
        session.close()


def test_uploading_batch_round_trips_through_the_column(factory):
    """`UPLOADING` 有 9 个字符，列是 String(16)——写得进、读得回、查得到。"""
    batch = import_service.create_upload_batch(
        site=SITE, period=PERIOD, report_date=RDATE,
        original_filename="US_x_Day_2026_07_18.csv", object_key=KEY,
        session_factory=factory,
    )
    assert batch.id is not None
    assert _status(factory, batch.id) == STATUS_UPLOADING


def test_claim_finds_the_uploading_batch_by_object_key(factory):
    created = import_service.create_upload_batch(
        site=SITE, period=PERIOD, report_date=RDATE,
        original_filename="US_x_Day_2026_07_18.csv", object_key=KEY,
        session_factory=factory,
    )
    assert import_service.claim_upload_batch(KEY, session_factory=factory) == created.id


def test_claim_returns_none_for_unknown_key(factory):
    assert import_service.claim_upload_batch(
        "imports/US/DAY/2026-07-18/nope.csv", session_factory=factory
    ) is None


def test_claim_ignores_batches_that_left_uploading(factory):
    """已经推进走的批次不该被再次认领——否则重复触发会把一条记录来回改写。"""
    created = import_service.create_upload_batch(
        site=SITE, period=PERIOD, report_date=RDATE,
        original_filename="US_x_Day_2026_07_18.csv", object_key=KEY,
        session_factory=factory,
    )
    session = factory()
    try:
        session.get(ImportBatch, created.id).status = STATUS_QUEUED
        session.commit()
    finally:
        session.close()

    assert import_service.claim_upload_batch(KEY, session_factory=factory) is None


def test_reap_leaves_fresh_uploads_alone(factory):
    """还在有效期内的上传不能被打扰——1.5GB 周报本来就传得久。"""
    bid = _make_uploading(factory, age=timedelta(minutes=5))
    import_service.reap_stale_uploads(
        object_size=lambda _k: pytest.fail("未超时的批次不该被称量"),
        max_file_size=3 * 1024**3, stale_seconds=3900, session_factory=factory,
    )
    assert _status(factory, bid) == STATUS_UPLOADING


def test_reap_marks_missing_object_failed(factory):
    bid = _make_uploading(factory, age=timedelta(hours=2))
    import_service.reap_stale_uploads(
        object_size=lambda _k: None,
        max_file_size=3 * 1024**3, stale_seconds=3900, session_factory=factory,
    )
    assert _status(factory, bid) == STATUS_FAILED


def test_reap_queues_uploaded_object_for_import(factory):
    """传完了没触发导入 → 转排队，交给既有接手者，不必重传。"""
    bid = _make_uploading(factory, age=timedelta(hours=2))
    import_service.reap_stale_uploads(
        object_size=lambda _k: 4096,
        max_file_size=3 * 1024**3, stale_seconds=3900, session_factory=factory,
    )
    assert _status(factory, bid) == STATUS_QUEUED

    # 转成 QUEUED 之后必须真的能被排队接手者看见——否则只是换了个死标签。
    session = factory()
    try:
        visible = session.execute(
            select(ImportBatch.id).where(
                ImportBatch.status == STATUS_QUEUED, ImportBatch.object_key != ""
            )
        ).scalars().all()
        assert bid in visible
    finally:
        session.close()
