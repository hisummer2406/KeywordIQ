"""业务服务健康探测：直接打业务 `app` 的本机端点，不经 nginx。

`ops-bot` 是 host 级服务（design.md D1 修订），与 `app` 容器的
`127.0.0.1:8000` 端口映射同处一台宿主机，可以直接访问，不需要额外的鉴权或
跨网络路径——`/health`、`/health/pool` 本来就是无鉴权端点（供轮询探活）。
"""

from __future__ import annotations

import httpx

_HEALTH_URL = "http://127.0.0.1:8000/health"
_POOL_URL = "http://127.0.0.1:8000/health/pool"
_TIMEOUT = 5.0


async def is_app_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            response = await client.get(_HEALTH_URL)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def pool_water_level() -> float | None:
    """两个引擎合计的 `checked_out / 总上限` 比例。探测本身失败返回 `None`——
    调用方不应把"探测不到"当作"水位为 0"，那会让服务真的挂了时巡检反而报告正常。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            response = await client.get(_POOL_URL)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    try:
        sync = data["sync_engine"]
        asy = data["async_engine"]
        ceiling = sync["pool_size"] + sync["max_overflow"] + asy["pool_size"] + asy["max_overflow"]
        checked_out = sync["checked_out"] + asy["checked_out"]
    except (KeyError, TypeError):
        return None

    if ceiling <= 0:
        return None
    return checked_out / ceiling
