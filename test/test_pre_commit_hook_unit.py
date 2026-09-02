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


def test_checks_run_concurrently_not_one_after_another(hook, monkeypatch):
    """并发是这条改动的全部意义——串行回去不会有任何检查失败，只是又慢回去。

    用"同时在跑的条数"作判据，不用总耗时：后者在负载高的机器上会假红。
    """
    import threading

    live = 0
    peak = 0
    guard = threading.Lock()
    gate = threading.Barrier(3, timeout=10)

    def fake_run(cmd, cwd, env=None, timeout=None):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        # 三条都到齐才放行；串行实现会在这里超时
        gate.wait()
        with guard:
            live -= 1
        return 0, ""

    monkeypatch.setattr(hook, "run", fake_run)
    checks = [(f"c{i}", ["cmd", str(i)], Path("."), {}) for i in range(3)]
    assert hook.run_checks(checks) == []
    assert peak == 3, f"检查没有并发跑（同时在跑的峰值只有 {peak}）"


def test_failures_keep_input_order_not_completion_order(hook, monkeypatch):
    """报告顺序必须只由传入顺序决定。

    按完成先后排的话，同一份改动两次提交会得到不同顺序的报告，读的人无从判断
    「这次和上次是不是同一批失败」。这里让先提交的那条故意最后完成。
    """
    import time

    def fake_run(cmd, cwd, env=None, timeout=None):
        if cmd == ["slow"]:
            time.sleep(0.2)
            return 1, "慢的失败了"
        return 1, "快的失败了"

    monkeypatch.setattr(hook, "run", fake_run)
    checks = [
        ("慢", ["slow"], Path("."), {}),
        ("快", ["fast"], Path("."), {}),
    ]
    assert [name for name, _, _ in hook.run_checks(checks)] == ["慢", "快"]


def test_only_failing_checks_are_reported(hook, monkeypatch):
    """通过的检查不该出现在报告里，否则失败会被淹没。"""
    monkeypatch.setattr(
        hook, "run", lambda cmd, cwd, env=None, timeout=None: (0, "") if cmd == ["ok"] else (1, "炸了")
    )
    checks = [
        ("好的", ["ok"], Path("."), {}),
        ("坏的", ["bad"], Path("."), {}),
    ]
    failures = hook.run_checks(checks)
    assert [name for name, _, _ in failures] == ["坏的"]
    assert "炸了" in failures[0][2]


def test_no_checks_is_not_an_error(hook):
    """没有可跑的检查（只改了文档）不该炸在线程池上。"""
    assert hook.run_checks([]) == []


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


# ---------------- 索引与工作树的分歧（divergent_files） ----------------
#
# 门禁验证的是**工作树**（`ruff check .` / `pytest` 读磁盘），提交的却是**索引**。
# 两者对同一个文件不一致时，刚跑绿的检查并不描述这次提交的内容——检查没报错，
# 只是查错了东西。
#
# 真实发生过：test/_gate_probe.py 被 staged 之后从磁盘删掉（索引状态 AD），
# 一次裸 `git commit` 把它提进了主干。它含一个未用 import，ruff 若看得见必报 F401，
# 但磁盘上没有那个文件，于是全程无人发现。


def _repo(tmp_path):
    """建一个最小仓库，返回路径。"""
    import subprocess as sp

    repo = tmp_path / "r"
    repo.mkdir()
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        sp.run(args, cwd=repo, check=True, capture_output=True)
    return repo


def _git(repo, *args):
    import subprocess as sp

    return sp.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_staged_then_deleted_from_worktree_is_divergent(hook, tmp_path):
    """AD：已暂存、磁盘已删。提交的内容从未被 lint 过——正是历史上那个洞。"""
    repo = _repo(tmp_path)
    (repo / "probe.py").write_text("import os\n")
    _git(repo, "add", "probe.py")
    (repo / "probe.py").unlink()

    assert hook.divergent_files(repo, "git commit -m x") == ["probe.py"]


def test_staged_then_edited_again_is_divergent(hook, tmp_path):
    """AM/MM：暂存后又改了。lint 看到的是新内容，提交的是旧内容。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    (repo / "a.py").write_text("x = 2\n")

    assert hook.divergent_files(repo, "git commit -m x") == ["a.py"]


def test_clean_staging_is_not_divergent(hook, tmp_path):
    """反向 fixture：暂存内容与磁盘一致——最常见的情形，绝不能误报。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")

    assert hook.divergent_files(repo, "git commit -m x") == []


def test_commit_all_is_never_divergent(hook, tmp_path):
    """`git commit -a` 在提交时把工作树一并暂存，提交的就是被 lint 的那份。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "add", "a.py")
    (repo / "a.py").write_text("x = 3\n")

    assert hook.divergent_files(repo, "git commit -am x") == []


def test_pathspec_commit_is_never_divergent(hook, tmp_path):
    """`git commit -- <路径>` 提交的是工作树内容，与 lint 到的一致。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    (repo / "a.py").write_text("x = 2\n")

    assert hook.divergent_files(repo, "git commit -m x -- a.py") == []


# ---------------------------------------------------------------------------
# staged_files：本次提交会带进去的文件
#
# 这一组守的是"门禁认不出改动 → 一条检查都不跑 → 静默放行"这条路。它与
# divergent_files 那一组容易混：那边问"提交的内容和被 lint 的内容是不是同一份"，
# 这边问"门禁到底看不看得见有改动"。看不见的后果更隐蔽——没有失败，也没有拦截，
# 输出与"全部检查通过"完全一样。
# ---------------------------------------------------------------------------


def test_pathspec_commit_sees_worktree_changes(hook, tmp_path):
    """`git commit -- <路径>` 提的是工作树内容，暂存区可以是空的。

    真实发生过（PR #66 的文案修复）：改了 20 个文件、一个都没 add，按路径提交，
    门禁查 `--cached` 查了个空，前端 lint/tsc/test 一条没跑就放行了。本仓库为并行
    会话推荐的恰恰是按路径提交，所以这不是边角形态，是主路径。
    """
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n")  # 只改工作树，不 add

    assert _git(repo, "diff", "--cached", "--name-only").stdout == ""
    assert hook.staged_files(repo, "git commit -m x -- a.py") == ["a.py"]


def test_commit_all_sees_worktree_changes(hook, tmp_path):
    """`git commit -a` 的既有行为不回归（同一条路的另一半）。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n")

    assert hook.staged_files(repo, "git commit -am x") == ["a.py"]


def test_plain_commit_only_sees_the_index(hook, tmp_path):
    """裸 `git commit` 提的是索引，工作树里的脏文件不该把无关检查拽进来。

    这条是上面两条的反面约束：不能为了"多查一点更安全"就无差别地并上工作树，
    否则同一个 worktree 里一份半成品会拦住一次与它无关的提交。
    """
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "add", "a.py")
    (repo / "b.py").write_text("y = 2\n")  # 未暂存，不属于本次提交

    assert hook.staged_files(repo, "git commit -m x") == ["a.py"]
