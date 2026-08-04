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


# ---------------- 提交落地的仓库（target_repo） ----------------
#
# 这一组守的是一个真实发生过的绕过：会话开在 worktree 里、命令 `cd 主仓 && git commit`，
# 门禁却拿 cwd（= worktree）去查暂存区，那里空空如也 → files 为空 → 直接放行。
# 一个未用 import 就是这样进的主干，而且悄无声息——门禁没报错，只是没查。

def test_cd_prefix_wins_over_session_cwd(hook, tmp_path):
    """`cd <repo> && git commit` 要按 cd 的目标查，不是按会话 cwd。"""
    target = tmp_path / "main-repo"
    target.mkdir()
    session_cwd = tmp_path / "worktree"
    session_cwd.mkdir()

    got = hook.target_repo(f"cd {target} && git add -A && git commit -m x", session_cwd)
    assert got == target


def test_git_dash_c_wins_over_session_cwd(hook, tmp_path):
    """`git -C <repo> commit` 同理。"""
    target = tmp_path / "main-repo"
    target.mkdir()
    session_cwd = tmp_path / "worktree"
    session_cwd.mkdir()

    got = hook.target_repo(f"git -C {target} commit -m x", session_cwd)
    assert got == target


def test_plain_commit_uses_session_cwd(hook, tmp_path):
    """没有切目录的命令仍按会话 cwd——反向 fixture，防止误改成总去猜路径。"""
    session_cwd = tmp_path / "repo"
    session_cwd.mkdir()

    assert hook.target_repo("git commit -m x", session_cwd) == session_cwd


def test_commit_message_mentioning_cd_is_not_a_path(hook, tmp_path):
    """提交信息里出现 'cd /somewhere' 不该被当成切目录。

    消息是一个整体 token，不会被拆成 ['cd', '/somewhere']——这条钉住该行为，
    免得有人把解析改成按空格粗切。
    """
    session_cwd = tmp_path / "repo"
    session_cwd.mkdir()

    assert hook.target_repo('git commit -m "cd /tmp 之后再提交"', session_cwd) == session_cwd


def test_nonexistent_path_falls_back_to_cwd(hook, tmp_path):
    """cd 的目标不存在时退回 cwd：宁可多查一遍，也不要静默不查。"""
    session_cwd = tmp_path / "repo"
    session_cwd.mkdir()

    got = hook.target_repo(f"cd {tmp_path / 'nope'} && git commit -m x", session_cwd)
    assert got == session_cwd


def test_unparsable_command_falls_back_to_cwd(hook, tmp_path):
    """引号不配对时不抛异常——门禁自己崩掉，连「该拦的没拦」都看不出来。"""
    session_cwd = tmp_path / "repo"
    session_cwd.mkdir()

    assert hook.target_repo('git commit -m "未闭合', session_cwd) == session_cwd
