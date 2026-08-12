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


def _build_app(monkeypatch, *, create_raises=False, recorder=None):
    import app.table.upload.upload_api as upload_api_mod
    from app.auth.simple_auth import simple_auth

    monkeypatch.setattr(
        upload_api_mod.oss_client, "issue_upload_credentials",
        lambda *a, **k: (recorder.append("issue") if recorder is not None else None) or _grant(),
    )

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
