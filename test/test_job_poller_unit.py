"""``app/jobs/poller.py`` 的单元测试：轮询编排本身，不连数据库。

这层此前完全没有测试覆盖——旧的 ``run_queue_drainer`` 直接写在 ``import_queue.py``
里，唯一被测的是它调用的 ``drain_import_queue`` / ``reap_stale_uploads`` 各自的实现，
循环调度逻辑（顺序、``continue`` 语义、异常隔离、取消）从未被验证过。见 openspec 变更
``extract-job-poller`` 的 ``design.md`` 与
``specs/background-job-poller/spec.md``。

**计时测试说明**：验证"该睡眠时确实睡了""不该睡眠时确实没睡"用的是真实短时间窗口 +
调用次数的宽松区间断言，而不是 mock ``asyncio.sleep``——mock 它会污染全局唯一的
``asyncio`` 模块对象，对同一事件循环里其他等待中的协程有副作用；用真实但很短的窗口
换来的是测试本身对 CI 抖动更宽容，代价是区间断言不是精确值，因此都留了充足的余量。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path

from app.jobs import poller


async def _run_and_cancel(jobs, *, interval_seconds: float = 0.0, settle: float = 0.05):
    """跑一小段真实时间后取消，等它真正退出。"""
    task = asyncio.create_task(poller.run_poller(jobs, interval_seconds, name="test"))
    await asyncio.sleep(settle)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return task


async def test_多个job按传入顺序依次执行():
    calls: list[str] = []

    async def a():
        calls.append("a")
        return False

    async def b():
        calls.append("b")
        return False

    async def c():
        calls.append("c")
        return False

    await _run_and_cancel([a, b, c])

    assert len(calls) >= 3
    assert calls[:3] == ["a", "b", "c"]


async def test_中间job返回True时跳过本轮剩余job():
    calls: list[str] = []

    async def a():
        calls.append("a")
        return False

    async def b():
        calls.append("b")
        await asyncio.sleep(0)  # 让出控制权，否则 interval=0 时整轮永不挂起，测试无法取消
        return True  # 恒真：排在它之后的 c 永远不该被执行到

    async def c():
        calls.append("c")
        return False

    await _run_and_cancel([a, b, c])

    assert "c" not in calls
    assert set(calls) == {"a", "b"}


async def test_连续多轮都有工作可做时不睡眠():
    calls: list[str] = []

    async def always_true():
        calls.append("x")
        # 真实 job 内部总会有一次会挂起的 await（线程池调用、网络 IO），这里用
        # sleep(0) 模拟"会让出控制权但不引入延迟"——如果没有这一下 yield，本函数会
        # 变成纯同步循环，事件循环拿不到任何执行机会，连测试自己的取消都发不出去。
        await asyncio.sleep(0)
        return True

    # interval 设得很大：如果中途真的睡过一次，0.05s 的窗口内不可能忙出这么多次调用
    await _run_and_cancel([always_true], interval_seconds=999)

    assert len(calls) >= 10


async def test_全部job返回False时按间隔睡眠():
    calls: list[str] = []

    async def a():
        calls.append("a")
        return False

    # interval=0.05，窗口 0.12s：预期落在 0/0.05/0.10 三个时刻各调用一次，共 3 次；
    # 留出调度抖动的余量，只要求落在一个远小于"忙跑"量级的区间内
    await _run_and_cancel([a], interval_seconds=0.05, settle=0.12)

    assert 2 <= len(calls) <= 5


async def test_某个job抛出异常时本轮剩余job不执行():
    calls: list[str] = []

    async def a():
        calls.append("a")
        raise ValueError("boom")

    async def b():
        calls.append("b")
        return False

    await _run_and_cancel([a, b], interval_seconds=0.05, settle=0.12)

    assert "a" in calls
    assert "b" not in calls  # a 每轮都抛异常，b 因此永远轮不到


async def test_某轮异常不影响后续轮次正常执行():
    attempts = 0
    b_calls = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("boom")
        return False

    async def b():
        nonlocal b_calls
        b_calls += 1
        return False

    await _run_and_cancel([flaky, b], interval_seconds=0.03, settle=0.15)

    assert attempts >= 2  # 第一轮异常之后，flaky 在后续轮次仍然被正常调用
    assert b_calls >= 1  # 且 b 在 flaky 不再抛异常之后能被执行到


async def test_轮询任务被外部取消时CancelledError透传():
    async def a():
        return False

    task = asyncio.create_task(poller.run_poller([a], 999, name="test"))
    await asyncio.sleep(0.02)  # 确保已经真正挂在 sleep(999) 上
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("run_poller 应当在被取消时向上抛出 CancelledError")

    assert task.cancelled()


def test_poller模块不引入任何领域模块():
    source = Path(poller.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    domain_imports = [m for m in imported_modules if m.startswith("app.table")]
    assert domain_imports == []
