import unittest

from config import Settings


class TestSettings(unittest.TestCase):
    def test_settings_load_from_environment(self):
        """Settings 能从环境装载，且 DEBUG 被强制成真正的 bool。

        原先这里断言 ``DEBUG is True``，并把它称作"默认配置值"——但 DEBUG 在 config.py
        里是**必填、没有默认**的，那个 True 来自开发机 .env。CI 显式设 DEBUG="False"，
        于是这条断言在 CI 上永远红、在本地永远绿：两边都测不出任何东西，只是把开发者
        个人的 .env 内容钉成了断言。

        换成断言类型与必填项存在——DEBUG 从字符串 "False" 强制成 bool 是 pydantic-settings
        的真实行为，且与环境取值无关。
        """
        settings = Settings()

        self.assertIsInstance(settings.DEBUG, bool)
        self.assertTrue(settings.APP_NAME)
        self.assertTrue(settings.DATABASE_URL)
        self.assertTrue(settings.DATABASE_SCHEMA)
