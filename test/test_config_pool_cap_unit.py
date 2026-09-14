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

from unittest.mock import patch

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


def test_宿主机cron进程读不到os_environ时回退读env文件里调高的值():
    """模拟 `backup_selfhosted_pg.py` 在宿主机 cron 里跑的场景。

    pydantic-settings 读 `.env` 只填充 `Settings` 已声明的字段，不会把
    `PG_MAX_CONNECTIONS` 这种未声明的 key 导出到 `os.environ`；宿主机 cron 进程
    因此看不到它（不像 `app` 容器，`env_file: .env` 会把它注入成真正的容器环境
    变量）。若 `.env` 把上限调高、池子四项也跟着调大但仍在新上限的安全余量内，
    只查 `os.environ` 会读到"没设置"而误按旧的 120 默认值判超限——
    `_LEGACY_RDS_POOL` 总量 240 超过 120-10=110，但在调高后的 400-10=390 内是
    安全的（Codex 审核 PR #84 第十八轮）。这里不接真实 `.env` 文件，直接把
    `dotenv.dotenv_values` 换成假实现，只验证回退路径本身。
    """
    with patch("dotenv.dotenv_values", return_value={"PG_MAX_CONNECTIONS": "400"}):
        settings = _settings(host="db", pool=_LEGACY_RDS_POOL)
    assert settings.DB_POOL_SIZE == 20


@pytest.mark.parametrize(
    "sentinel_pool",
    [
        {**_NEW_SELF_HOSTED_POOL, "DB_POOL_SIZE": 0},
        {**_NEW_SELF_HOSTED_POOL, "DB_ASYNC_POOL_SIZE": 0},
        {**_NEW_SELF_HOSTED_POOL, "DB_MAX_OVERFLOW": -1},
        {**_NEW_SELF_HOSTED_POOL, "DB_ASYNC_MAX_OVERFLOW": -1},
    ],
)
def test_host为db时拒绝SQLAlchemy无界池哨兵值(sentinel_pool):
    """`pool_size<=0`/`max_overflow=-1` 是 SQLAlchemy `QueuePool` 表示"不限制"的
    哨兵值，不是字面的 0 或负数；按算术字面求和会把它们当成 0 或负数计入总量，
    让实际不受限的连接池从总量校验里蒙混过去（Codex 审核 PR #84 第二十一轮）。
    这几组取值单独看总量算式都不超 110，必须靠专门的哨兵值校验挡住，不能指望
    总量算式本身发现问题。"""
    with pytest.raises(ValueError, match="不限连接数的哨兵值"):
        _settings(host="db", pool=sentinel_pool)


def test_host不是db时不受哨兵值校验约束():
    """本地开发/RDS 路线不受自建 Postgres 连接数硬顶约束，同样不该被这条哨兵值
    校验挡住——它是自建部署那条校验的一部分，不是独立生效的通用规则。"""
    settings = _settings(
        host="localhost", pool={**_NEW_SELF_HOSTED_POOL, "DB_MAX_OVERFLOW": -1}
    )
    assert settings.DB_MAX_OVERFLOW == -1


def test_os_environ与env文件都没有时仍回退到120默认值():
    """两个来源都取不到 `PG_MAX_CONNECTIONS` 时（`.env` 里从没写过这一项），
    不能让 `dotenv_values(...).get(...)` 返回 `None` 时的 `int(None)` 直接炸——
    必须落回 docker-compose.yml 同款的 120 默认值。"""
    with patch("dotenv.dotenv_values", return_value={}):
        with pytest.raises(ValueError, match="超过自建 PostgreSQL 的安全上限"):
            _settings(host="db", pool=_LEGACY_RDS_POOL)  # 240 > 120-10=110，仍应拒绝


def test_只改DATABASE_URL_ASYNC指向自建库而DATABASE_URL仍留在RDS时启动拒绝():
    """切库若只改了一半——这是本条校验要挡住的真实场景。若不拒绝，池子总量校验
    会因为只看 `DATABASE_URL` 的 host（仍是 RDS，非 `db`）直接 return self 放行，
    异步引擎实际已经指向自建库却完全不受连接数上限约束；`/health` 对两个引擎各自
    `SELECT 1`，两边分别健康就都探活通过，同样发现不了这个分裂（Codex 审核 PR #84
    第二十三轮）。"""
    with pytest.raises(ValueError, match="指向了不同的数据库"):
        Settings(
            DATABASE_URL=(
                "postgresql://postgres:pwd123@"
                "pgm-example-rds-host.pg.rds.aliyuncs.com/amazon_search"
            ),
            DATABASE_URL_ASYNC="postgresql+asyncpg://postgres:pwd123@db/amazon_search",
            **_LEGACY_RDS_POOL,
        )


def test_同步与异步URL的host相同但库名不同时启动拒绝():
    """host 一致不代表指向同一份数据——库名也必须一致，否则两个引擎各自读写
    的是同一台 Postgres 实例上互不相关的两个数据库。"""
    with pytest.raises(ValueError, match="指向了不同的数据库"):
        Settings(
            DATABASE_URL="postgresql://postgres:pwd123@db/amazon_search",
            DATABASE_URL_ASYNC="postgresql+asyncpg://postgres:pwd123@db/other_db",
            **_NEW_SELF_HOSTED_POOL,
        )


def test_同步与异步URL一致且host不是db时不受此校验影响():
    """两个 URL 一致是这条新校验唯一放行的条件；host 是否为 `db` 只影响后面的池子
    总量校验，两者独立。"""
    settings = _settings(host="localhost", pool=_LEGACY_RDS_POOL)
    assert settings.DATABASE_URL.endswith("localhost/amazon_search")
