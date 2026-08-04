"""提交门禁自身的单元测试（不连库、不起子进程）。

门禁是没人测的测试器——这正是它烂掉的方式。这里守两条：

- **子进程必须有超时。** 卡住的 pytest（咨询锁抢锁、半开连接，本仓库都出过）会让
  提交无限期挂起，且没有任何输出——表现是 agent"不动了"，而不是"检查失败了"。
- **测试库探活要探到库，不是探到端口。** 本机跑着别的 PostgreSQL 时端口是通的，
  于是门禁把 TEST_DATABASE_URL 喂给集成测试，得到一堆与本次改动无关的失败。
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_HOOK_PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "pre_commit.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("pre_commit_hook", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def hook():
    return _load_hook()


def test_run_passes_a_timeout(hook, monkeypatch):
    """没有 timeout 的 subprocess.run 会让卡住的检查把提交挂死。"""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    hook.run(["true"], Path("."))

    assert seen.get("timeout"), "subprocess.run 没有 timeout"


def test_timeout_is_reported_as_failure_not_a_hang(hook, monkeypatch):
    """超时要变成一条可读的失败，而不是异常穿出去或静默放行。

    异常穿出去 → 钩子进程崩掉 → 宿主拿不到 deny，提交照过；静默放行同理。
    卡住这件事本身就是"这次提交不该过"的信号。
    """

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    code, out = hook.run(["pytest"], Path("."))

    assert code != 0
    assert "超时" in out


def test_postgres_up_requires_the_test_database(hook, monkeypatch):
    """端口通不等于测试库在。本机跑着别的 PostgreSQL 时端口一样是通的。"""
    monkeypatch.setattr(hook, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(hook, "_test_db_reachable", lambda: False)

    assert hook.postgres_up() is False


def test_postgres_up_true_when_test_database_answers(hook, monkeypatch):
    monkeypatch.setattr(hook, "_port_open", lambda *a, **k: True)
    monkeypatch.setattr(hook, "_test_db_reachable", lambda: True)

    assert hook.postgres_up() is True
