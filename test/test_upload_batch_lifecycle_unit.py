"""直传批次生命周期的单元测试——不依赖数据库与真实 OSS。

守住的是一句话：**「上传中」这个状态必须诚实**。

它诞生于一个具体的坏体验：直传中断（关标签页、断网、分片上传最后一步失败）在界面上
完全无感——OSS 里没有完整对象，库里没有任何记录，用户以为传上去了，历史屏也查不到。
修法是签发凭证时就落记录；而落了记录就欠一个收尾，否则「上传中」只是把「什么都不显示」
换成了一句永远不会兑现的话（`QUEUED` 曾经就是这么烂的）。

因此这里测的三件事都关于「不撒谎」：
- 记录落不下就不要签发凭证（否则用户拿着能用的凭证去传一个服务端不知情的文件）；
- 认领已有记录，不让一次上传在历史里留下两条；
- 超时的回收要按「对象到底在不在」分流，而不是一律判失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.table.upload import import_service
from app.table.upload.import_batch_model import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_UPLOADING,
    VALID_STATUSES,
)

# --------------------------------------------------------------------------
# 极简的批次替身与会话替身：回收逻辑只碰 status / error_message / object_key /
# created_at 四个字段，用真 ORM 对象反而要拖一个数据库进来。
# --------------------------------------------------------------------------


@dataclass
class FakeBatch:
    id: int = 1
    object_key: str = "imports/US/DAY/2026-07-18/abc_US_x_Day_2026_07_18.csv"
    file_name: str = "US_x_Day_2026_07_18.csv"
    status: str = STATUS_UPLOADING
    error_message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """只支持 execute/commit/close，够回收与认领两条路径用。"""

    def __init__(self, rows):
        self._rows = rows
        self.commits = 0
        self.closed = False

    def execute(self, _stmt):
        return FakeResult(self._rows)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def session_factory_for(rows):
    session = FakeSession(rows)
    return lambda: session, session


class TestStatusVocabulary:
    def test_uploading_is_a_valid_status(self):
        """状态词表漏了它，历史屏的 valueEnum 与筛选会静默少一档。"""
        assert STATUS_UPLOADING in VALID_STATUSES


class TestReapStaleUploads:
    """回收的三条分支。"""

    def _reap(self, rows, *, object_size, max_file_size=3 * 1024**3, stale_seconds=3900):
        factory, session = session_factory_for(rows)
        handled = import_service.reap_stale_uploads(
            object_size=object_size,
            max_file_size=max_file_size,
            stale_seconds=stale_seconds,
            session_factory=factory,
        )
        return handled, session

    def test_object_missing_is_marked_failed(self):
        """传到一半断了：OSS 里没有完整对象，判失败并说清楚要重传。"""
        batch = FakeBatch(created_at=datetime.now(UTC) - timedelta(hours=2))
        handled, session = self._reap([batch], object_size=lambda _k: None)

        assert handled == [batch]
        assert batch.status == STATUS_FAILED
        assert "重新上传" in batch.error_message
        assert session.commits == 1

    def test_object_present_is_queued_not_failed(self):
        """字节传完了、只是没触发导入（关标签页的典型形态）。

        **不能判失败**——文件已经在 OSS 里，让用户重传一遍 1.5GB 毫无道理。转入排队，
        由既有的接手者导入。
        """
        batch = FakeBatch(created_at=datetime.now(UTC) - timedelta(hours=2))
        handled, _ = self._reap([batch], object_size=lambda _k: 1024)

        assert handled == [batch]
        assert batch.status == STATUS_QUEUED
        assert batch.error_message == ""

    def test_oversized_object_is_rejected_not_imported(self):
        """绕过 /oss/import 不能绕过大小上限。

        上限的执行点本来在导入触发接口里，而这条路径压根没经过它——不在这里补一道，
        「不调 /oss/import」就成了一条把超大文件送进导入的路子。
        """
        batch = FakeBatch(created_at=datetime.now(UTC) - timedelta(hours=2))
        handled, _ = self._reap(
            [batch], object_size=lambda _k: 5 * 1024**3, max_file_size=3 * 1024**3
        )

        assert handled == [batch]
        assert batch.status == STATUS_FAILED
        assert "上限" in batch.error_message

    def test_size_lookup_failure_does_not_stop_the_round(self):
        """称量对象要走网络，可能抛。

        一条抛了不能让整轮停下——否则一个坏对象会挡住它后面所有批次的收尾，而那正是
        「没人收尾」这个病本身。判失败并写明「无法确认」，下一轮不再看它。
        """
        boom = FakeBatch(id=1, created_at=datetime.now(UTC) - timedelta(hours=2))
        ok = FakeBatch(id=2, created_at=datetime.now(UTC) - timedelta(hours=2))

        def flaky(_key):
            if flaky.calls == 0:
                flaky.calls += 1
                raise RuntimeError("连接超时")
            return 1024

        flaky.calls = 0

        handled, _ = self._reap([boom, ok], object_size=flaky)

        assert handled == [boom, ok]
        assert boom.status == STATUS_FAILED
        assert "无法确认" in boom.error_message
        assert ok.status == STATUS_QUEUED, "前一条出错不该影响后面的批次"

    def test_batch_without_object_key_is_failed(self):
        """没有 object key 就无从确认，也无从重导。留在 UPLOADING 等于撒谎。"""
        batch = FakeBatch(object_key="", created_at=datetime.now(UTC) - timedelta(hours=2))
        handled, _ = self._reap([batch], object_size=lambda _k: pytest.fail("不该被称量"))

        assert handled == [batch]
        assert batch.status == STATUS_FAILED

    def test_nothing_to_reap_does_not_commit(self):
        """没有超时批次时不该白提交一次事务。"""
        _, session = self._reap([], object_size=lambda _k: 1024)
        assert session.commits == 0


class TestStaleThreshold:
    def test_threshold_is_derived_from_credential_lifetime(self):
        """阈值必须由凭证有效期推导，不能是一个能与之矛盾的独立旋钮。

        判据不是「多久算久」，而是「还有没有可能传完」：凭证一过期，后续分片一律被拒，
        这次上传再也完成不了。两个独立旋钮迟早矛盾，而两个方向都糟——短于凭证有效期会
        把正在好好传的 1.5GB 周报判死，远长于它则让用户白等几十分钟才看到失败。
        """
        from config import settings

        assert settings.upload_stale_seconds > settings.OSS_STS_DURATION_SECONDS, (
            "阈值必须严格大于凭证有效期，否则会把仍在传输中的上传判死"
        )
        assert (
            settings.upload_stale_seconds
            == settings.OSS_STS_DURATION_SECONDS + settings.UPLOAD_STALE_GRACE_SECONDS
        )
