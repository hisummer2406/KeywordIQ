"""业务服务健康探测：直接打业务 `app` 的本机端点，不经 nginx。

`ops-bot` 是 host 级服务（design.md D1 修订），与 `app` 容器的端口映射同处一台
宿主机，可以直接访问，不需要额外的鉴权或跨网络路径——`/health`、`/health/pool`
本来就是无鉴权端点（供轮询探活）。

地址取 `settings.OPS_APP_BASE_URL`（默认 V2 的 8001）。V2 与 V1 同机，8000 是 V1：
写死 8000 的后果是 `/health` 探到 V1（V2 挂了也报健康）、`/health/pool` 在 V1 上
不存在（每轮"探测失败"），两者都不会报错，只是探错了对象。
"""

from __future__ import annotations

import httpx

from config import settings

_TIMEOUT = 5.0


def _url(path: str) -> str:
    return f"{settings.OPS_APP_BASE_URL.rstrip('/')}{path}"


async def is_app_healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            response = await client.get(_url("/health"))
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def pool_water_level() -> float | None:
    """两个引擎合计的 `checked_out / 总上限` 比例。探测本身失败返回 `None`——
    调用方不应把"探测不到"当作"水位为 0"，那会让服务真的挂了时巡检反而报告正常。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            response = await client.get(_url("/health/pool"))
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
