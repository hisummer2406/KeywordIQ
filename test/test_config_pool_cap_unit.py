"""`Settings._validate_pool_within_self_hosted_pg_cap` 的单元测试（不连库）。

回归目标：独立 RDS（max_connections=400）升级到自建部署（docker-compose 的 `db`
服务，`max_connections` 默认 120）后，若 `.env` 里 RDS 时期的
`DB_POOL_SIZE=20`/`DB_MAX_OVERFLOW=10`/`DB_ASYNC_POOL_SIZE=20`/
`DB_ASYNC_MAX_OVERFLOW=10` 原样留着未同步调小，(20+10+20+10)×4=240 会远超自建
Postgres 的连接上限——异步查询池单独就能在健康检查、导入、管理连接之前吃光全部
配额（Codex 审核 PR #84 第十五轮）。这条校验只在 `DATABASE_URL` 的 host 恰好是
`db`（自建部署的标志）时才启用，本地开发（`localhost`）与仍在 RDS 路线的部署
不受影响——这是本仓库自己的本地 `.env` 就留着旧的 20/10 这一事实决定的，按 host
收窄是唯一不会把本地开发一起挡在启动之外的做法。
"""

from __future__ import annotations

import pytest

from config import Settings

_LEGACY_RDS_POOL = {
    "DB_POOL_SIZE": 20,
    "DB_MAX_OVERFLOW": 10,
    "DB_ASYNC_POOL_SIZE": 20,
    "DB_ASYNC_MAX_OVERFLOW": 10,
}
_NEW_SELF_HOSTED_POOL = {
    "DB_POOL_SIZE": 2,
    "DB_MAX_OVERFLOW": 3,
    "DB_ASYNC_POOL_SIZE": 8,
    "DB_ASYNC_MAX_OVERFLOW": 8,
}


def _settings(*, host: str, pool: dict) -> Settings:
    return Settings(
        DATABASE_URL=f"postgresql://postgres:pwd123@{host}/amazon_search",
        DATABASE_URL_ASYNC=f"postgresql+asyncpg://postgres:pwd123@{host}/amazon_search",
        **pool,
    )


@pytest.fixture(autouse=True)
def _no_pg_max_connections_env(monkeypatch):
    """默认不设 PG_MAX_CONNECTIONS，逼校验走 docker-compose.yml 同款 120 默认值。"""
    monkeypatch.delenv("PG_MAX_CONNECTIONS", raising=False)


def test_host为db且沿用RDS旧池子配置时启动拒绝():
    with pytest.raises(ValueError, match="超过自建 PostgreSQL 的安全上限"):
        _settings(host="db", pool=_LEGACY_RDS_POOL)


def test_host为db且用新的自建起点值时正常启动():
    settings = _settings(host="db", pool=_NEW_SELF_HOSTED_POOL)
    assert settings.DB_POOL_SIZE == 2


def test_host不是db时即便沿用RDS旧池子配置也不受这条校验约束(monkeypatch):
    """本地开发（localhost）与仍在 RDS 路线的部署都不该被这条自建专属校验挡住。"""
    settings = _settings(host="localhost", pool=_LEGACY_RDS_POOL)
    assert settings.DB_POOL_SIZE == 20

    settings_rds = _settings(
        host="pgm-example-rds-host.pg.rds.aliyuncs.com", pool=_LEGACY_RDS_POOL
    )
    assert settings_rds.DB_POOL_SIZE == 20


def test_显式调高PG_MAX_CONNECTIONS后旧池子配置放行(monkeypatch):
    monkeypatch.setenv("PG_MAX_CONNECTIONS", "400")
    settings = _settings(host="db", pool=_LEGACY_RDS_POOL)
    assert settings.DB_POOL_SIZE == 20


def test_未设PG_MAX_CONNECTIONS时按docker_compose同款120默认值算余量():
    """紧贴安全上限（110=120-10）应放行，超一点点就该拒绝——不是靠一个随意的大差值蒙混过关。"""
    # (26+2+8+8)×4=176，工作量太大——改用更小的、恰好卡在边界两侧的取值。
    # 总量 = (a+b+c+d)×4，要卡在 110 边界：a+b+c+d = 27（108，放行）或 28（112，拒绝）。
    ok_pool = {"DB_POOL_SIZE": 2, "DB_MAX_OVERFLOW": 3, "DB_ASYNC_POOL_SIZE": 8, "DB_ASYNC_MAX_OVERFLOW": 14}
    _settings(host="db", pool=ok_pool)  # 108 ≤ 110，不应抛错

    over_pool = {"DB_POOL_SIZE": 2, "DB_MAX_OVERFLOW": 3, "DB_ASYNC_POOL_SIZE": 8, "DB_ASYNC_MAX_OVERFLOW": 15}
    with pytest.raises(ValueError, match="超过自建 PostgreSQL 的安全上限"):
        _settings(host="db", pool=over_pool)  # 112 > 110，应抛错
