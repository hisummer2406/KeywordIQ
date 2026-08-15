"""签发直传凭证与触发导入两个端点的契约——TestClient + 依赖覆盖，不连库、不连 OSS。

两条都是「顺序错了不会报错，只会静默留下一个坏状态」的那种，所以值得单测钉住：

1. **落库在签发之前。** 落库失败还把凭证交出去，用户手上就是一份能用的凭证配一条不存在
   的记录——那正是这次改动要消灭的状态（传上去了、服务端不知道）。
2. **触发导入要认领已有批次。** 不认领的话一次上传在历史里留下两条记录：一条永远停在
   「上传中」，一条从排队开始，用户看不出它们是同一件事。
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.error_handlers import register_error_handlers

FILENAME = "US_SearchTerm_Day_2026_07_18.csv"
OBJECT_KEY = "imports/US/DAY/2026-07-18/abc123_US_SearchTerm_Day_2026_07_18.csv"


def _super_user():
    from app.user.user_model import UserCenter

    return UserCenter(id=1, user_name="root", hashed_pwd="x", is_active=True, is_super=True)


def _grant():
    from app.table.upload.oss_client import UploadGrant

    return UploadGrant(
        object_key=OBJECT_KEY, bucket="b", endpoint="e", region="r",
        access_key_id="ak", access_key_secret="sk", security_token="st",
        expiration="2026-07-18T00:00:00Z",
    )


def _build_app(monkeypatch, *, create_raises=False, recorder=None, issued=None):
    import app.table.upload.upload_api as upload_api_mod
    from app.auth.simple_auth import simple_auth

    def fake_issue(*a, **k):
        if recorder is not None:
            recorder.append("issue")
        if issued is not None:
            issued.append(k)
        # 续签沿用传入的 key；新上传才生成新 key
        existing = k.get("existing_key")
        grant = _grant()
        if existing:
            grant.object_key = existing
        return grant

    monkeypatch.setattr(upload_api_mod.oss_client, "issue_upload_credentials", fake_issue)

    def fake_create(**kwargs):
        if recorder is not None:
            recorder.append("create")
        if create_raises:
            raise RuntimeError("db down")
        return type("B", (), {"id": 42})()

    monkeypatch.setattr(upload_api_mod, "create_upload_batch", fake_create)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(upload_api_mod.upload_router, prefix="/api/upload")
    app.dependency_overrides[simple_auth.get_current_user] = _super_user
    return app


class TestCredentialsCreateBatch:
    def test_response_carries_batch_id(self, monkeypatch):
        client = TestClient(_build_app(monkeypatch))
        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US"})
        assert r.status_code == 200
        assert r.json()["batch_id"] == 42

    def test_no_credentials_when_batch_write_fails(self, monkeypatch):
        """落库失败就不交凭证——否则用户会开始一次服务端不知情的上传。

        这条同时钉住了「落库先于返回」这个顺序：签发在前还是在后无关紧要，要紧的是
        **凭证不能在记录落下之前被交出去**。正常路径上两种顺序看不出任何区别，只有
        落库失败这一次才暴露，而那一次用户已经拿着凭证开始传了。
        """
        client = TestClient(_build_app(monkeypatch, create_raises=True),
                            raise_server_exceptions=False)
        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US"})

        assert r.status_code >= 500
        body = r.json()
        assert body.get("success") is False
        assert "access_key_id" not in body and "security_token" not in body


class TestRefreshDoesNotCreateBatch:
    """续签凭证不是一次新上传，MUST NOT 新增批次记录。

    大文件直传耗时可能超过凭证有效期，前端每 5 分钟续签一次。曾经续签走的是与新上传
    完全相同的路径，于是一次 25 分钟的周报上传在历史里留下 5 条记录——其中 4 条的
    object key 从未被写入过任何对象，65 分钟后被回收判成 FAILED「请重新上传」，
    而文件其实传成功了。
    """

    def test_refresh_creates_no_batch(self, monkeypatch):
        recorder: list[str] = []
        client = TestClient(_build_app(monkeypatch, recorder=recorder))

        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US", "object_key": OBJECT_KEY})

        assert r.status_code == 200
        assert "create" not in recorder, "续签不该落批次记录"
        assert recorder == ["issue"]

    def test_refresh_reuses_the_same_object_key(self, monkeypatch):
        """续签沿用同一个 key——换 key 会把一个文件传成两个对象。"""
        issued: list[dict] = []
        client = TestClient(_build_app(monkeypatch, issued=issued))

        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US", "object_key": OBJECT_KEY})

        assert r.json()["object_key"] == OBJECT_KEY
        assert issued[0].get("existing_key") == OBJECT_KEY

    def test_refresh_still_enforces_prefix(self, monkeypatch):
        """续签不能成为「为任意 key 取得写凭证」的入口——它签发的是**写**权限。"""
        recorder: list[str] = []
        client = TestClient(_build_app(monkeypatch, recorder=recorder))

        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US",
                              "object_key": "imports/DE/DAY/2026-07-18/evil_x.csv"})

        assert r.status_code == 400
        assert recorder == [], "越权应在签发之前被拒"
        body = r.json()
        assert body.get("success") is False
        assert "access_key_id" not in body

    def test_new_upload_still_creates_batch(self, monkeypatch):
        """不带 object_key 的既有行为逐字不变。"""
        recorder: list[str] = []
        client = TestClient(_build_app(monkeypatch, recorder=recorder))

        r = client.post("/api/upload/oss/upload-credentials",
                        json={"filename": FILENAME, "site": "US"})

        assert r.status_code == 200
        assert "create" in recorder
        assert r.json()["batch_id"] == 42


class TestImportClaimsBatch:
    def test_import_claims_the_uploading_batch(self, monkeypatch):
        """触发导入时按 object key 认领，run_import 应收到那条批次的 id。"""
        import app.table.upload.upload_api as upload_api_mod

        seen: dict = {}

        monkeypatch.setattr(upload_api_mod, "claim_upload_batch", lambda key: 42)
        monkeypatch.setattr(
            upload_api_mod, "run_import",
            lambda **kw: seen.update(kw) or type("B", (), {"status": "COMPLETED"})(),
        )
        monkeypatch.setattr(upload_api_mod.oss_client, "object_stream", lambda *a, **k: None)

        upload_api_mod._run_oss_import_bg(
            OBJECT_KEY, FILENAME, "US", "DAY", date(2026, 7, 18)
        )
        assert seen["batch_id"] == 42

    def test_import_still_works_when_nothing_to_claim(self, monkeypatch):
        """认领不到是正常情形（回收刚判失败、或跳过签发直接导入）。

        这时应新建而不是报错：这个端点的契约是「凭 key 触发导入」，它不该因为另一条
        链路的记账状态而变得不可用。
        """
        import app.table.upload.upload_api as upload_api_mod

        seen: dict = {}

        monkeypatch.setattr(upload_api_mod, "claim_upload_batch", lambda key: None)
        monkeypatch.setattr(
            upload_api_mod, "run_import",
            lambda **kw: seen.update(kw) or type("B", (), {"status": "COMPLETED"})(),
        )
        monkeypatch.setattr(upload_api_mod.oss_client, "object_stream", lambda *a, **k: None)

        upload_api_mod._run_oss_import_bg(
            OBJECT_KEY, FILENAME, "US", "DAY", date(2026, 7, 18)
        )
        assert seen["batch_id"] is None
