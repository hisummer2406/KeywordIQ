"""竞对监控（关注清单）集成测试。

默认跳过，只在设置 TEST_DATABASE_URL 时运行。测试库必须已迁移到 head。每个测试独占
一个事务，结束即回滚——service 层内部会 commit，故用 SAVEPOINT 承接（与
test_keyword_library_integration.py 同一模式）。

    TEST_DATABASE_URL=postgresql://postgres:pwd123@localhost/keywordiq_test \\
        uv run pytest test/test_competitor_watch_integration.py -v

这里钉的是三件不接真库就验不了的事：**append 模式不覆盖已有分组/备注**、
**replace_group 清空重建**、**配额在预览阶段拦截**。
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.table.competitor import watch_service as service
from app.table.competitor.watch_schemas import (
    CommitRequest,
    GroupCreateRequest,
    GroupRenameRequest,
    WatchListRequest,
    WatchUpdateRequest,
)
from config import settings

TEST_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="设置 TEST_DATABASE_URL 指向一次性测试库以运行数据库集成测试"
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _tables_exist_sync() -> bool:
    from sqlalchemy import create_engine

    engine = create_engine(TEST_URL)
    try:
        with engine.begin() as conn:
            return conn.execute(
                text("SELECT to_regclass('analysis.user_seller_watch')")
            ).scalar() is not None
    finally:
        engine.dispose()


@pytest.fixture()
async def db():
    if not _tables_exist_sync():
        pytest.skip("测试库未迁移，请先运行 alembic upgrade head")

    engine = create_async_engine(_async_url(TEST_URL), poolclass=NullPool)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


def _seed(db):
    def _user(name):
        return db.execute(
            text("INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                 "VALUES (:n,'x',true,false) RETURNING id"),
            {"n": name},
        ).scalar()

    owner = _user("cw-owner")
    other = _user("cw-other")
    db.flush()
    return {"owner": owner, "other": other}


_SAMPLE_TEXT = (
    "https://www.amazon.com/s?i=merchant-items&me=AS2G99DA1EUF&s=date-desc-rank"
    "&ds=v1%3Adummy&marketplaceID=ATVPDKIKX0DER\n"
    "https://www.amazon.com/s?i=merchant-items&me=AS2G99DA1EUF&s=date-desc-rank"
    "&ds=v1%3Adummy&marketplaceID=ATVPDKIKX0DER\n"  # 重复一次，应折算进 priority
    "https://www.amazon.co.uk/s?i=merchant-items&me=A16U556F7GIJ50&s=date-desc-rank"
    "&ds=v1%3Adummy&marketplaceID=A1F83G8C2ARO7P\n"
)


async def _preview(db, user_id, text_=_SAMPLE_TEXT, fallback_site=None):
    return await service.preview(
        db, user_id, raw_text=text_, bookmark_html=None, fallback_site=fallback_site
    )


# ---------- 分组 ----------


async def test_同名分组冲突(db):
    seed = await db.run_sync(_seed)
    await service.create_group(db, seed["owner"], GroupCreateRequest(name="头部竞对"))
    with pytest.raises(service.GroupDuplicateError):
        await service.create_group(db, seed["owner"], GroupCreateRequest(name="头部竞对"))


async def test_改名后重名仍冲突(db):
    seed = await db.run_sync(_seed)
    await service.create_group(db, seed["owner"], GroupCreateRequest(name="A组"))
    group_b = await service.create_group(db, seed["owner"], GroupCreateRequest(name="B组"))
    with pytest.raises(service.GroupDuplicateError):
        await service.rename_group(db, seed["owner"], group_b.group_id, GroupRenameRequest(name="A组"))


async def test_跨用户分组不可见(db):
    seed = await db.run_sync(_seed)
    group = await service.create_group(db, seed["owner"], GroupCreateRequest(name="头部竞对"))
    with pytest.raises(service.GroupNotFoundError):
        await service._require_group(db, seed["other"], group.group_id)


async def test_删除分组不取消关注(db):
    seed = await db.run_sync(_seed)
    group = await service.create_group(db, seed["owner"], GroupCreateRequest(name="头部竞对"))
    preview = await _preview(db, seed["owner"])
    await service.commit(
        db, seed["owner"],
        CommitRequest(preview_token=preview.preview_token, mode="append", target_group_id=group.group_id),
    )

    await service.delete_group(db, seed["owner"], group.group_id)

    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    assert resp.total == 2, "关注关系不应随分组一起被删除"
    assert all(r.group_id is None for r in resp.data), "删组后应变为未分组，而不是保留悬空 group_id"


# ---------- 预览 / 确认两步 ----------


async def test_预览不写库(db):
    seed = await db.run_sync(_seed)
    await _preview(db, seed["owner"])

    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    assert resp.total == 0, "预览阶段不应产生任何关注关系"


async def test_重复链接折算进priority(db):
    seed = await db.run_sync(_seed)
    preview = await _preview(db, seed["owner"])
    assert preview.parsed == 2, "两个不同卖家，重复的那条应被折算而不是算作第三条"
    assert preview.new == 2

    await service.commit(
        db, seed["owner"], CommitRequest(preview_token=preview.preview_token, mode="append"),
    )
    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    by_seller = {r.seller_id: r for r in resp.data}
    assert by_seller["AS2G99DA1EUF"].priority == 2
    assert by_seller["A16U556F7GIJ50"].priority == 1
    assert by_seller["AS2G99DA1EUF"].site == "US"
    assert by_seller["A16U556F7GIJ50"].site == "GB", "UK marketplaceID 必须映射成 GB 不是 UK"


async def test_append模式不覆盖已有分组与备注(db):
    seed = await db.run_sync(_seed)
    group_a = await service.create_group(db, seed["owner"], GroupCreateRequest(name="A组"))
    preview1 = await _preview(db, seed["owner"])
    await service.commit(
        db, seed["owner"],
        CommitRequest(preview_token=preview1.preview_token, mode="append", target_group_id=group_a.group_id),
    )
    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    watch_id = next(r.watch_id for r in resp.data if r.seller_id == "AS2G99DA1EUF")
    await service.update_watch(db, seed["owner"], watch_id, WatchUpdateRequest(note="手动备注"))

    # 再次导入同一批（不指定 target_group_id）——已存在的应保留分组与备注，只更新 priority
    preview2 = await _preview(db, seed["owner"])
    await service.commit(db, seed["owner"], CommitRequest(preview_token=preview2.preview_token, mode="append"))

    resp2 = await service.list_watches(db, seed["owner"], WatchListRequest())
    row = next(r for r in resp2.data if r.seller_id == "AS2G99DA1EUF")
    assert row.group_id == group_a.group_id, "append 模式不应覆盖已有分组"
    assert row.note == "手动备注", "append 模式不应覆盖已有备注"


async def test_replace_group模式清空重建(db):
    seed = await db.run_sync(_seed)
    group = await service.create_group(db, seed["owner"], GroupCreateRequest(name="替换组"))

    preview1 = await _preview(db, seed["owner"], text_="A16U556F7GIJ50\n", fallback_site="GB")
    await service.commit(
        db, seed["owner"],
        CommitRequest(preview_token=preview1.preview_token, mode="append", target_group_id=group.group_id),
    )
    resp = await service.list_watches(db, seed["owner"], WatchListRequest(group_id=group.group_id))
    assert resp.total == 1

    preview2 = await _preview(db, seed["owner"])  # 两个不同的卖家，不含上面那个
    result = await service.commit(
        db, seed["owner"],
        CommitRequest(
            preview_token=preview2.preview_token, mode="replace_group", target_group_id=group.group_id
        ),
    )
    assert result.removed_watches == 1

    resp2 = await service.list_watches(db, seed["owner"], WatchListRequest(group_id=group.group_id))
    assert resp2.total == 2
    assert {r.seller_id for r in resp2.data} == {"AS2G99DA1EUF", "A16U556F7GIJ50"}


async def test_配额在预览阶段拦截(db, monkeypatch):
    seed = await db.run_sync(_seed)
    monkeypatch.setattr(settings, "COMPETITOR_MAX_WATCH_PER_USER", 1)
    with pytest.raises(service.CompetitorLimitError):
        await _preview(db, seed["owner"])  # 本批解析出 2 个卖家，超过上限 1

    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    assert resp.total == 0, "超配额时不应写入任何关注关系"


async def test_过期预览拒绝确认(db, monkeypatch):
    seed = await db.run_sync(_seed)
    monkeypatch.setattr(settings, "COMPETITOR_PREVIEW_TTL_SECONDS", -1)
    preview = await _preview(db, seed["owner"])

    with pytest.raises(service.PreviewExpiredError):
        await service.commit(db, seed["owner"], CommitRequest(preview_token=preview.preview_token))

    resp = await service.list_watches(db, seed["owner"], WatchListRequest())
    assert resp.total == 0, "过期预览确认失败时不应发生任何写入"


async def test_跨用户预览token不可确认(db):
    seed = await db.run_sync(_seed)
    preview = await _preview(db, seed["owner"])
    with pytest.raises(service.PreviewNotFoundError):
        await service.commit(db, seed["other"], CommitRequest(preview_token=preview.preview_token))


async def test_裸ID需要fallback_site才能预览成功(db):
    seed = await db.run_sync(_seed)
    preview = await _preview(db, seed["owner"], text_="A1TZ946F8HZTNY\n", fallback_site=None)
    assert preview.parsed == 0
    assert preview.unparsable[0].text == "A1TZ946F8HZTNY"

    preview2 = await _preview(db, seed["owner"], text_="A1TZ946F8HZTNY\n", fallback_site="US")
    assert preview2.parsed == 1
    assert not preview2.unparsable


def test_清理过期预览行():
    """``purge_expired_previews`` 自建独立的同步连接，不能用 SAVEPOINT 隔离的 ``db``
    fixture 验证（另一个连接看不见未提交的事务）。这里改为真实提交 + 用完手动清理。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(TEST_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        user_id = session.execute(
            text("INSERT INTO analysis.user_center(user_name,hashed_pwd,is_active,is_super) "
                 "VALUES ('cw-purge-test','x',true,false) RETURNING id")
        ).scalar()
        expired_token = "expired-" + os.urandom(4).hex()
        live_token = "live-" + os.urandom(4).hex()
        session.execute(
            text("INSERT INTO analysis.competitor_watch_preview"
                 "(preview_token,user_id,parsed,expires_at) VALUES (:tok,:u,'{}'::jsonb,:exp)"),
            {"tok": expired_token, "u": user_id, "exp": datetime.now(UTC) - timedelta(seconds=1)},
        )
        session.execute(
            text("INSERT INTO analysis.competitor_watch_preview"
                 "(preview_token,user_id,parsed,expires_at) VALUES (:tok,:u,'{}'::jsonb,:exp)"),
            {"tok": live_token, "u": user_id, "exp": datetime.now(UTC) + timedelta(hours=1)},
        )
        session.commit()

        service.purge_expired_previews(session_factory=Session)

        remaining = session.execute(
            text("SELECT preview_token FROM analysis.competitor_watch_preview WHERE user_id = :u"),
            {"u": user_id},
        ).scalars().all()
        assert remaining == [live_token], "只应清理过期行，未过期的行应保留"
    finally:
        session.execute(
            text("DELETE FROM analysis.competitor_watch_preview WHERE user_id IN "
                 "(SELECT id FROM analysis.user_center WHERE user_name = 'cw-purge-test')")
        )
        session.execute(text("DELETE FROM analysis.user_center WHERE user_name = 'cw-purge-test'"))
        session.commit()
        session.close()
        engine.dispose()
