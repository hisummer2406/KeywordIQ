"""OSS-first 导入的单元测试——用桩替代 OSS/STS，不依赖真实对象存储与数据库。

守住几处"错了不会立刻报错"的安全逻辑：
- 直传临时凭证的 RAM 策略必须最小权限（限定 bucket + key 前缀 + 仅写操作），过宽=越权风险；
- 前端回传的 object key 必须落在其被授权的前缀内，否则可越权触发导入其他站点/期次的切片；
- 对象不存在时导入源必须拒绝（不产生部分导入）；
- 导入源抽象（本地流 / OSS 流）要能统一驱动导入内核的结构校验，且按 utf-8-sig 正确解码。
"""

from __future__ import annotations

import io
import json
import tempfile
from datetime import date

import pytest

from app.table.upload import oss_client
from app.table.upload.import_source import LocalFileSource, OSSObjectSource, basename
from app.table.upload.importer import ImportError_, validate_structure

# --------------------------------------------------------------------------
# object key 生成与前缀限权
# --------------------------------------------------------------------------


class TestObjectKey:
    def test_prefix_encodes_site_period_date(self):
        prefix = oss_client.key_prefix_for("US", "DAY", date(2026, 7, 18), prefix="imports")
        assert prefix == "imports/US/DAY/2026-07-18/"

    def test_prefix_normalizes_site(self):
        # 英国用 GB；小写/空格都规整。
        assert oss_client.key_prefix_for(" gb ", "WEEK", date(2026, 7, 18), prefix="imports") == \
            "imports/GB/WEEK/2026-07-18/"

    def test_build_key_is_within_prefix_and_keeps_basename(self):
        key = oss_client.build_object_key("US", "DAY", date(2026, 7, 18), "US_x_Day_2026_07_18.csv", prefix="imports")
        assert key.startswith("imports/US/DAY/2026-07-18/")
        assert key.endswith("_US_x_Day_2026_07_18.csv")

    def test_build_key_strips_path_traversal(self):
        key = oss_client.build_object_key("US", "DAY", date(2026, 7, 18), "../../etc/passwd.csv", prefix="imports")
        assert "/../" not in key
        assert key.endswith("_passwd.csv")

    def test_basename_strips_windows_and_posix_paths(self):
        assert basename("a/b/c.csv") == "c.csv"
        assert basename("a\\b\\c.csv") == "c.csv"


class TestPrefixAuthorization:
    def test_accepts_key_in_authorized_prefix(self):
        key = "imports/US/DAY/2026-07-18/ab12_x.csv"
        assert oss_client.is_key_within_allowed_prefix(key, "US", "DAY", date(2026, 7, 18)) is True

    def test_rejects_key_of_other_site(self):
        key = "imports/GB/DAY/2026-07-18/ab12_x.csv"
        assert oss_client.is_key_within_allowed_prefix(key, "US", "DAY", date(2026, 7, 18)) is False

    def test_rejects_key_of_other_date(self):
        key = "imports/US/DAY/2026-07-19/ab12_x.csv"
        assert oss_client.is_key_within_allowed_prefix(key, "US", "DAY", date(2026, 7, 18)) is False


# --------------------------------------------------------------------------
# 直传凭证签发：最小权限策略
# --------------------------------------------------------------------------


@pytest.fixture()
def oss_configured(monkeypatch):
    """把 OSS 配成"已启用"，供签发凭证的用例使用。"""
    s = oss_client.settings
    monkeypatch.setattr(s, "OSS_ENABLED", True)
    monkeypatch.setattr(s, "OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setattr(s, "OSS_REGION", "cn-hangzhou")
    monkeypatch.setattr(s, "OSS_BUCKET", "kiq-imports")
    monkeypatch.setattr(s, "OSS_KEY_PREFIX", "imports")
    monkeypatch.setattr(s, "OSS_ACCESS_KEY_ID", "AK")
    monkeypatch.setattr(s, "OSS_ACCESS_KEY_SECRET", "SK")
    monkeypatch.setattr(s, "OSS_STS_ROLE_ARN", "acs:ram::123:role/kiq-uploader")
    monkeypatch.setattr(s, "OSS_STS_DURATION_SECONDS", 900)


class TestIssueCredentials:
    def test_policy_is_least_privilege(self, oss_configured):
        captured = {}

        def fake_assume(role_arn, session_name, policy, duration):
            captured["role_arn"] = role_arn
            captured["policy"] = policy
            captured["duration"] = duration
            return {"access_key_id": "STS_AK", "access_key_secret": "STS_SK",
                    "security_token": "TOKEN", "expiration": "2026-07-18T00:00:00Z"}

        grant = oss_client.issue_upload_credentials(
            "US", "DAY", date(2026, 7, 18), "US_x_Day_2026_07_18.csv", assume_role=fake_assume
        )

        # 目标信息回传正确。
        assert grant.bucket == "kiq-imports"
        assert grant.object_key.startswith("imports/US/DAY/2026-07-18/")
        assert grant.security_token == "TOKEN"

        # 策略最小权限：只允许写、只限本次前缀、短有效期。
        policy = json.loads(captured["policy"])
        stmt = policy["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Resource"] == ["acs:oss:*:*:kiq-imports/imports/US/DAY/2026-07-18/*"]
        assert set(stmt["Action"]) == {
            "oss:PutObject", "oss:InitiateMultipartUpload", "oss:UploadPart",
            "oss:CompleteMultipartUpload", "oss:AbortMultipartUpload",
        }
        # 不得包含读/删/列举等越权动作。
        assert not any(a in stmt["Action"] for a in ("oss:GetObject", "oss:DeleteObject", "oss:ListObjects"))
        assert captured["duration"] == 900
        assert captured["role_arn"] == "acs:ram::123:role/kiq-uploader"

    def test_raises_when_oss_disabled(self, monkeypatch):
        monkeypatch.setattr(oss_client.settings, "OSS_ENABLED", False)
        with pytest.raises(oss_client.OSSNotConfigured):
            oss_client.issue_upload_credentials("US", "DAY", date(2026, 7, 18), "x.csv")


# --------------------------------------------------------------------------
# 对象存在性 / 流式打开
# --------------------------------------------------------------------------


class _FakeBucket:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects

    def object_exists(self, key: str) -> bool:
        return key in self._objects

    def get_object(self, key: str):
        return io.BytesIO(self._objects[key])


class TestObjectAccess:
    def test_missing_object_raises(self):
        bucket = _FakeBucket({})
        with pytest.raises(oss_client.OSSObjectNotFound):
            oss_client.object_stream("imports/US/DAY/2026-07-18/x.csv", bucket=bucket)

    def test_existing_object_streams(self):
        bucket = _FakeBucket({"k": b"hello"})
        stream = oss_client.object_stream("k", bucket=bucket)
        assert stream.read() == b"hello"

    def test_object_exists_delegates(self):
        bucket = _FakeBucket({"k": b""})
        assert oss_client.object_exists("k", bucket=bucket) is True
        assert oss_client.object_exists("missing", bucket=bucket) is False


# --------------------------------------------------------------------------
# 导入源抽象：本地流与 OSS 流统一驱动结构校验
# --------------------------------------------------------------------------


def _valid_csv_bytes() -> bytes:
    header = "搜索频率排名,搜索词," + ",".join(f"c{i}" for i in range(19))
    data = "1,obsession," + ",".join([""] * 19)
    return ('报告范围=["每日"],选择日期=["2026-07-18"]\n' + header + "\n" + data + "\n").encode("utf-8-sig")


class TestImportSource:
    def test_oss_source_reads_utf8_sig_text(self):
        payload = "报告范围\n第二行\n".encode("utf-8-sig")
        src = OSSObjectSource(lambda k: io.BytesIO(payload), "k")
        with src.open_text() as f:
            first = f.readline()
        # BOM 应被 utf-8-sig 解码吃掉，不残留在文本里。
        assert first.startswith("报告范围")
        assert "﻿" not in first

    def test_oss_source_closes_underlying_stream(self):
        closed = {"v": False}

        class _Stream(io.BytesIO):
            def close(self):
                closed["v"] = True
                super().close()

        src = OSSObjectSource(lambda k: _Stream(b"a\nb\nc\n"), "k")
        with src.open_text() as f:
            f.readline()
        assert closed["v"] is True

    def test_validate_structure_passes_for_oss_source(self):
        src = OSSObjectSource(lambda k: io.BytesIO(_valid_csv_bytes()), "k")
        validate_structure(src)  # 不抛即通过

    def test_validate_structure_rejects_bad_oss_source(self):
        bad = "随便一行\n又一行\n再一行\n".encode("utf-8-sig")
        src = OSSObjectSource(lambda k: io.BytesIO(bad), "k")
        with pytest.raises(ImportError_):
            validate_structure(src)

    def test_local_and_oss_sources_agree_on_valid_structure(self):
        payload = _valid_csv_bytes()
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as fh:
            fh.write(payload)
            path = fh.name
        # 两种来源都应无异常通过同一份内容。
        validate_structure(LocalFileSource(path))
        validate_structure(OSSObjectSource(lambda k: io.BytesIO(payload), "k"))


class TestImportMetaResolution:
    """`_resolve_import_meta`：一次导入的归属三元组由文件名判定，界面只做兜底。

    三元组决定数据写进哪张事实表的哪个分区，错了就是数据落到查不到的归属下，所以这里的
    每一条"判定不出就报错"都是刻意的——默认值在这个位置等同于静默写错。
    """

    @staticmethod
    def _resolve(filename: str, site: str = "US", period=None):
        from app.table.upload.upload_api import _resolve_import_meta

        return _resolve_import_meta(filename, site, period)

    def test_full_inference_from_filename(self):
        site, period, rdate = self._resolve("DE_热门搜索词_简单_Day_2026_07_16.csv", site="DE")
        assert (site, period, rdate) == ("DE", "DAY", date(2026, 7, 16))

    def test_week_inferred(self):
        _, period, _ = self._resolve("GB_weekly_2026_01_05.csv", site="GB")
        assert period == "WEEK"

    def test_undecidable_period_is_rejected_not_defaulted(self):
        """关键回归：此前这种文件名会被静默当成周报写进 fact_weekly。"""
        from app.common.errors import BadRequestError

        with pytest.raises(BadRequestError) as exc:
            self._resolve("US_热门搜索词_2026_07_18.csv")
        assert "周期" in exc.value.message

    def test_explicit_period_conflicting_with_filename_is_rejected(self):
        from app.common.errors import BadRequestError

        with pytest.raises(BadRequestError):
            self._resolve("US_热门搜索词_简单_Day_2026_07_18.csv", period="WEEK")

    def test_explicit_period_fills_the_undecidable_case(self):
        _, period, _ = self._resolve("US_热门搜索词_2026_07_18.csv", period="WEEK")
        assert period == "WEEK"

    def test_site_conflict_still_rejected(self):
        from app.common.errors import BadRequestError

        with pytest.raises(BadRequestError) as exc:
            self._resolve("DE_热门搜索词_简单_Day_2026_07_16.csv", site="US")
        assert "DE" in exc.value.message
