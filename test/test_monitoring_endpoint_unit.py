"""系统监控端点的单元测试（无需 PostgreSQL）。

守两件事，都是实测出过问题的：

1. **它不阻塞事件循环。** psutil 的 `cpu_percent(interval=...)` 是同步阻塞采样，
   写在 `async def` 里就是让整个 worker 停摆同等时长。这条路径被轮询，且 4 个
   worker 共享 4 vCPU，停摆是可被用户感知的。
2. **它要鉴权。** 端点挂在 `/api` 前缀下由 nginx 反代出去，返回的是宿主机的
   CPU / 内存 / 磁盘 / 进程数——不鉴权等于对外公开机器状态。

不进入 lifespan（不用 `with TestClient(...)`），因此不会连库。
"""
import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import monitoring
from app.auth.simple_auth import simple_auth


@pytest.fixture()
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture()
def fresh_monitor(monkeypatch):
    """给端点换一个全新的 SystemMonitor。

    `main.system_monitor` 是进程内长驻单例（这正是它要的语义），但对测试而言那意味着
    样本计数与"上次采样时刻"会从别的测试漏进来——采样闸一旦生效，前一个测试刚采过的
    时间戳会让本测试的第一次请求也被挡掉，失败信息则完全看不出是污染。
    """
    monitor = monitoring.SystemMonitor()
    monkeypatch.setattr(main, "system_monitor", monitor)
    return monitor


def _fake_cpu_percent(sleep_for: float = 0.0):
    """假采样：形状与 psutil 一致（percpu 给列表），可选地真睡一会儿。"""

    def sample(interval=None, percpu=False):
        if sleep_for:
            time.sleep(sleep_for)
        return [1.0, 2.0] if percpu else 1.5

    return sample


def test_metrics_requires_authentication(client):
    """无令牌必拒。此前该端点没有任何鉴权依赖，全局兜底也不存在。"""
    r = client.get("/api/monitoring/metrics")

    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_metrics_does_not_block_the_event_loop(monkeypatch):
    """采样期间事件循环必须还能跑别的协程。

    用一个会睡 0.2 秒的假采样代替 psutil：真实采样是 `interval=1`，测试里没必要
    真等一秒，要验的是"这段阻塞有没有占住事件循环"，与阻塞多久无关。
    """
    monkeypatch.setattr(monitoring.psutil, "cpu_percent", _fake_cpu_percent(sleep_for=0.2))

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    transport = httpx.ASGITransport(app=main.app)
    main.app.dependency_overrides[simple_auth.get_current_user] = lambda: object()
    tick_task = asyncio.create_task(ticker())
    try:
        await asyncio.sleep(0.02)  # 让 ticker 先跑起来
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.get("/api/monitoring/metrics")
    finally:
        tick_task.cancel()
        main.app.dependency_overrides.clear()

    assert r.status_code == 200

    # 阻塞 0.2 秒、心跳 0.01 秒：不占事件循环的话至少能跳十几次
    assert ticks > 5, f"采样期间事件循环被占住了（只跳了 {ticks} 次）"


async def _get_metrics_twice() -> tuple[dict, dict]:
    """连打两次监控端点，返回两份响应体。"""
    transport = httpx.ASGITransport(app=main.app)
    main.app.dependency_overrides[simple_auth.get_current_user] = lambda: object()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            return (
                (await ac.get("/api/monitoring/metrics")).json(),
                (await ac.get("/api/monitoring/metrics")).json(),
            )
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_summary_accumulates_across_calls(monkeypatch, fresh_monitor):
    """摘要要能跨请求累积。

    此前每个请求都新建一个 SystemMonitor，`metrics_history` 永远只有一个样本，
    "摘要"实际上在总结它自己刚采的那一个点。

    采样闸在这里关掉（间隔置 0），否则两次请求会共用同一份样本——那是另一条性质，
    由 `test_sampling_is_throttled` 单独覆盖。两条断言混在一个测试里的话，一旦失败
    分不清是"单例没累积"还是"闸没放行"。
    """
    monkeypatch.setattr(monitoring.psutil, "cpu_percent", _fake_cpu_percent())
    monkeypatch.setattr(monitoring, "MIN_SAMPLE_INTERVAL_SECONDS", 0)

    first, second = await _get_metrics_twice()

    assert second["summary"]["samples"] == first["summary"]["samples"] + 1


@pytest.mark.asyncio
async def test_sampling_is_throttled(monkeypatch, fresh_monitor):
    """闸内的第二次请求复用上一份样本，不再真的采一次。

    每次真实采样占住一条 anyio 线程池线程整整一个采样窗口，而那个池与 bcrypt 登录、
    CSV 导出、同步导入共用。端点只要求"已登录"，没有这道闸时任何账号都能靠轮询
    把线程池占满。
    """
    calls = 0
    fake = _fake_cpu_percent()

    def counting_cpu_percent(*args, **kwargs):
        nonlocal calls
        calls += 1
        return fake(*args, **kwargs)

    monkeypatch.setattr(monitoring.psutil, "cpu_percent", counting_cpu_percent)
    monkeypatch.setattr(monitoring, "MIN_SAMPLE_INTERVAL_SECONDS", 300)

    first, second = await _get_metrics_twice()

    assert calls == 1, f"闸内应只采一次，实际采了 {calls} 次"
    # 复用的是同一份样本，故时间戳一致、样本计数不增长
    assert second["current_metrics"]["timestamp"] == first["current_metrics"]["timestamp"]
    assert second["summary"]["samples"] == first["summary"]["samples"]
