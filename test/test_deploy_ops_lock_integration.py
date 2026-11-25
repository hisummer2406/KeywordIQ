"""deploy.sh 的运维动作锁（scripts/deploy_ops_lock.sh）与导入认领的真实互斥（#209 P1）。

用真实 PostgreSQL 驱动脚本里的 bash 函数，验证两个方向：
- 导入已进入 PROCESSING/UPLOADING 时发布拒绝执行，且不留锁；
- 发布先持锁时导入认领返回 OPS_BUSY，批次留在 QUEUED；发布结束锁即释放。

只在 TEST_DATABASE_URL 时运行；需要 psql，本机没有 psql 时可设
TEST_PSQL_DOCKER_CONTAINER=<容器名>，改用 ``docker exec`` 进容器跑 psql。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_URL = os.environ.get("TEST_DATABASE_URL")
DOCKER_CONTAINER = os.environ.get("TEST_PSQL_DOCKER_CONTAINER")
HAS_PSQL = bool(shutil.which("psql")) or bool(DOCKER_CONTAINER)
pytestmark = pytest.mark.skipif(
    not (TEST_URL and HAS_PSQL), reason="需要 TEST_DATABASE_URL 和 psql（或 TEST_PSQL_DOCKER_CONTAINER）"
)

ROOT = Path(__file__).resolve().parents[1]
LOCK_KEY = 0x4B4F


def _psql_function() -> str:
    """生成替换脚本里 ``ops_lock_psql`` 的 bash 函数：连到测试库，而不是生产的 db 容器。"""
    url = urlparse(TEST_URL)
    flags = "-X -q -At -v ON_ERROR_STOP=1"
    if DOCKER_CONTAINER:
        db = url.path.lstrip("/")
        user = url.username or "postgres"
        return (
            "ops_lock_psql() { docker exec -i -e PGAPPNAME=\"$1\" "
            f"{shlex.quote(DOCKER_CONTAINER)} psql {flags} -U {shlex.quote(user)} -d {shlex.quote(db)}; }}"
        )
    return f"ops_lock_psql() {{ PGAPPNAME=\"$1\" psql {flags} {shlex.quote(TEST_URL)}; }}"


def _start_holder() -> subprocess.Popen:
    """起一个 bash：取锁 → 打印结果 → 等 stdin 一行 → 释放 → 打印 RELEASED。"""
    script = "\n".join([
        "set -euo pipefail",
        f". {shlex.quote(str(ROOT / 'scripts/deploy_ops_lock.sh'))}",
        _psql_function(),
        "if ops_lock_acquire; then echo ACQUIRED; else echo FAILED; exit 0; fi",
        "read -r _",
        "ops_lock_release",
        "echo RELEASED",
    ])
    return subprocess.Popen(
        ["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )


@pytest.fixture()
def factory():
    engine = create_engine(TEST_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE analysis.import_batch RESTART IDENTITY CASCADE"))
    yield sessionmaker(bind=engine, expire_on_commit=False)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE analysis.import_batch RESTART IDENTITY CASCADE"))
    engine.dispose()


def _lock_free(engine) -> bool:
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        conn.commit()
    return bool(got)


def _insert_batch(factory, status: str) -> int:
    with factory() as session:
        batch_id = session.execute(
            text("INSERT INTO analysis.import_batch (site, period_type, report_date, status) "
                 "VALUES ('US', 'DAY', :d, :s) RETURNING id"),
            {"d": date(2026, 11, 1), "s": status},
        ).scalar_one()
        session.commit()
    return batch_id


def test_deploy_holds_lock_so_import_claim_backs_off(factory):
    from app.table.upload.import_batch_model import ImportBatch
    from app.table.upload.import_service import _OPS_BUSY, _claim_processing_slot

    batch_id = _insert_batch(factory, "QUEUED")
    holder = _start_holder()
    try:
        assert holder.stdout.readline().strip() == "ACQUIRED", holder.stderr.read()

        with factory() as session:
            batch = session.get(ImportBatch, batch_id)
            assert _claim_processing_slot(session, batch) == _OPS_BUSY
            status = session.execute(
                text("SELECT status FROM analysis.import_batch WHERE id = :i"), {"i": batch_id}
            ).scalar_one()
            assert status == "QUEUED"

        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.stdout.readline().strip() == "RELEASED"
    finally:
        holder.kill()
        holder.wait(timeout=10)

    assert _lock_free(factory.kw["bind"])


@pytest.mark.parametrize("status", ["PROCESSING", "UPLOADING"])
def test_deploy_refuses_when_import_already_active(factory, status):
    _insert_batch(factory, status)
    holder = _start_holder()
    out, err = holder.communicate(timeout=60)
    assert out.strip() == "FAILED"
    assert "IMPORT_ACTIVE" in err
    # 复核失败后 psql 退出，已拿到的锁不能残留，否则会把后续导入一直挡在 QUEUED。
    assert _lock_free(factory.kw["bind"])


def test_deploy_refuses_when_lock_is_taken(factory):
    engine = factory.kw["bind"]
    with engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        other.commit()
        holder = _start_holder()
        out, err = holder.communicate(timeout=60)
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        other.commit()
    assert out.strip() == "FAILED"
    assert "OPS_LOCK_BUSY" in err


def test_lock_released_when_deploy_script_dies(factory):
    """deploy.sh 被 kill（Actions 取消等）时，FIFO 写端随进程关闭，锁不残留。"""
    holder = _start_holder()
    assert holder.stdout.readline().strip() == "ACQUIRED", holder.stderr.read()
    holder.kill()
    holder.wait(timeout=10)

    import time

    engine = factory.kw["bind"]
    for _ in range(30):
        if _lock_free(engine):
            return
        time.sleep(0.5)
    pytest.fail("deploy 进程被 kill 后运维动作锁未在 15 秒内释放")
