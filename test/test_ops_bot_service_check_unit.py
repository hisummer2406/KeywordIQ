"""业务服务健康探测的单元测试：打桩 httpx，不打真网络。"""

from __future__ import annotations

import unittest

import httpx

from ops_bot.checks.service import is_app_healthy, pool_water_level

# 先存一份未打桩的真实 AsyncClient——下面每个用例都会 `patch("httpx.AsyncClient", ...)`
# 顶替全局符号，`_client_with` 若直接调用 `httpx.AsyncClient(...)` 会调到打桩后的
# 自己，无限递归。
_RealAsyncClient = httpx.AsyncClient


def _client_with(handler) -> httpx.AsyncClient:
    return _RealAsyncClient(transport=httpx.MockTransport(handler))


class TestIsAppHealthy(unittest.IsolatedAsyncioTestCase):
    async def test_200_is_healthy(self):
        def handler(request):
            return httpx.Response(200, json={"status": "healthy"})

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", lambda **kw: _client_with(handler)):
            self.assertTrue(await is_app_healthy())

    async def test_503_is_unhealthy(self):
        def handler(request):
            return httpx.Response(503)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", lambda **kw: _client_with(handler)):
            self.assertFalse(await is_app_healthy())

    async def test_connection_error_is_unhealthy(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", lambda **kw: _client_with(handler)):
            self.assertFalse(await is_app_healthy())


class TestPoolWaterLevel(unittest.IsolatedAsyncioTestCase):
    async def test_computes_combined_ratio(self):
        payload = {
            "sync_engine": {"pool_size": 2, "max_overflow": 3, "checked_out": 2},
            "async_engine": {"pool_size": 8, "max_overflow": 8, "checked_out": 6},
        }

        def handler(request):
            return httpx.Response(200, json=payload)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", lambda **kw: _client_with(handler)):
            ratio = await pool_water_level()

        # ceiling = 2+3+8+8 = 21, checked_out = 2+6 = 8
        self.assertAlmostEqual(ratio, 8 / 21)

    async def test_probe_failure_returns_none_not_zero(self):
        """探测失败不能被当成"水位 0%"——那会让服务真挂了时巡检反而报告正常。"""

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", lambda **kw: _client_with(handler)):
            self.assertIsNone(await pool_water_level())


if __name__ == "__main__":
    unittest.main()
