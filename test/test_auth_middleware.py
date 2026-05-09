import unittest
from app.auth.auth_middleware import AdminAuthMiddleware


class TestAdminAuthMiddleware(unittest.TestCase):
    def setUp(self):
        self.middleware = AdminAuthMiddleware(app=None)

    def test_admin_paths_not_protected(self):
        # /admin/* 由 amis 框架自行处理，中间件放行
        for path in ["/admin/login", "/admin/user", "/admin/dashboard"]:
            self.assertFalse(self.middleware._needs_auth(path), path)

    def test_exclude_paths_not_protected(self):
        for path in ["/api/auth/login", "/api/auth/profile",
                     "/static/style.css", "/docs", "/health", "/"]:
            self.assertFalse(self.middleware._needs_auth(path), path)

    def test_unknown_paths_not_protected(self):
        for path in ["/unknown/path", "/api/public/data"]:
            self.assertFalse(self.middleware._needs_auth(path), path)

    def test_get_user_sync_is_callable(self):
        # _get_user_sync 必须是可调用的静态方法，供 asyncio.to_thread 使用
        self.assertTrue(callable(AdminAuthMiddleware._get_user_sync))
