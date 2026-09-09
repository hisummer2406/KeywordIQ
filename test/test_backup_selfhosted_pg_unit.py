"""`backup_selfhosted_pg.main` 失败清理路径的单元测试（不连库、不连容器）。

回归目标：`pg_dump`/`docker compose cp` 半途失败时，容器内那个 PID 专属的归档文件
此前不会被清理——本脚本设计成按 cron 定时反复失败重跑（比如 OSS 凭证配错），不清理
会让每次失败都在容器可写层里新增一份数据库量级的文件，累计写满宿主机磁盘（Codex
审核 PR #84 第十四轮）。用 monkeypatch 替身验证：dump/copy 任一失败都必须清理容器
产物，且清理不能掩盖最初的备份错误；清理自身失败时也不能掩盖最初的错误。
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

import scripts.backup_selfhosted_pg as backup_mod
from config import settings


@pytest.fixture(autouse=True)
def _oss_enabled(monkeypatch):
    monkeypatch.setattr(settings, "OSS_ENABLED", True)
    monkeypatch.setattr("sys.argv", ["backup_selfhosted_pg.py"])


def test_dump失败时清理容器并原样抛出原始异常(monkeypatch):
    calls: list[str] = []
    dump_error = subprocess.CalledProcessError(1, ["pg_dump"])

    def fake_dump(*a, **k):
        calls.append("dump")
        raise dump_error

    def fake_copy(*a, **k):
        calls.append("copy")

    def fake_cleanup(*a, **k):
        calls.append("cleanup")

    monkeypatch.setattr(backup_mod, "_dump_full", fake_dump)
    monkeypatch.setattr(backup_mod, "_copy_out", fake_copy)
    monkeypatch.setattr(backup_mod, "_cleanup_container", fake_cleanup)
    monkeypatch.setattr(backup_mod, "_upload", MagicMock())

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        backup_mod.main()

    assert exc_info.value is dump_error
    assert calls == ["dump", "cleanup"]
    backup_mod._upload.assert_not_called()


def test_copy失败时清理容器并原样抛出原始异常(monkeypatch):
    calls: list[str] = []
    copy_error = subprocess.CalledProcessError(1, ["docker", "compose", "cp"])

    def fake_dump(*a, **k):
        calls.append("dump")

    def fake_copy(*a, **k):
        calls.append("copy")
        raise copy_error

    def fake_cleanup(*a, **k):
        calls.append("cleanup")

    monkeypatch.setattr(backup_mod, "_dump_full", fake_dump)
    monkeypatch.setattr(backup_mod, "_copy_out", fake_copy)
    monkeypatch.setattr(backup_mod, "_cleanup_container", fake_cleanup)
    monkeypatch.setattr(backup_mod, "_upload", MagicMock())

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        backup_mod.main()

    assert exc_info.value is copy_error
    assert calls == ["dump", "copy", "cleanup"]
    backup_mod._upload.assert_not_called()


def test_清理自身失败也不掩盖原始备份错误(monkeypatch):
    dump_error = ValueError("dump boom")

    def fake_dump(*a, **k):
        raise dump_error

    def fake_cleanup(*a, **k):
        raise subprocess.CalledProcessError(1, ["rm"])

    monkeypatch.setattr(backup_mod, "_dump_full", fake_dump)
    monkeypatch.setattr(backup_mod, "_copy_out", MagicMock())
    monkeypatch.setattr(backup_mod, "_cleanup_container", fake_cleanup)
    monkeypatch.setattr(backup_mod, "_upload", MagicMock())

    with pytest.raises(ValueError) as exc_info:
        backup_mod.main()

    assert exc_info.value is dump_error


def test_成功时清理只执行一次且顺序在dump与copy之后上传之前(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_dump(*a, **k):
        calls.append("dump")

    def fake_copy(compose_service, local_path, dump_path_in_container):
        calls.append("copy")
        local_path.write_bytes(b"fake-dump-bytes")

    def fake_cleanup(*a, **k):
        calls.append("cleanup")

    def fake_upload(*a, **k):
        calls.append("upload")

    monkeypatch.setattr(backup_mod, "_dump_full", fake_dump)
    monkeypatch.setattr(backup_mod, "_copy_out", fake_copy)
    monkeypatch.setattr(backup_mod, "_cleanup_container", fake_cleanup)
    monkeypatch.setattr(backup_mod, "_upload", fake_upload)

    exit_code = backup_mod.main()

    assert exit_code == 0
    assert calls == ["dump", "copy", "cleanup", "upload"]
