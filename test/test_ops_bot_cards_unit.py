"""飞书卡片模板的单元测试：只验证结构与字段完整性，不验证真实渲染效果
（tasks.md 已标注为生产/飞书实机待验证项）。
"""

from __future__ import annotations

import unittest

from ops_bot.feishu.cards import (
    build_confirm_card,
    build_result_card,
    build_status_card,
    parse_button_value,
)


class TestBuildStatusCard(unittest.TestCase):
    def test_contains_title_and_lines(self):
        card = build_status_card(title="每日巡检摘要", lines=["服务：正常", "磁盘：62%"])

        self.assertEqual(card["header"]["title"]["content"], "每日巡检摘要")
        self.assertIn("磁盘：62%", card["elements"][0]["text"]["content"])


class TestBuildConfirmCard(unittest.TestCase):
    def test_both_buttons_carry_same_task_and_token(self):
        card = build_confirm_card(
            action="发布",
            task_id="t-1",
            confirm_token="tok-1",
            summary_lines=["当前 SHA: abc", "目标 SHA: def"],
        )

        buttons = card["elements"][1]["actions"]
        self.assertEqual(len(buttons), 2)
        for button in buttons:
            self.assertEqual(button["value"]["task_id"], "t-1")
            self.assertEqual(button["value"]["confirm_token"], "tok-1")

        decisions = {b["value"]["decision"] for b in buttons}
        self.assertEqual(decisions, {"confirm", "cancel"})


class TestBuildResultCard(unittest.TestCase):
    def test_success_uses_green_template(self):
        card = build_result_card(action="重启", success=True, output_excerpt="ok")

        self.assertEqual(card["header"]["template"], "green")
        self.assertIn("成功", card["header"]["title"]["content"])

    def test_failure_uses_red_template(self):
        card = build_result_card(action="重启", success=False, output_excerpt="boom")

        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("失败", card["header"]["title"]["content"])


class TestParseButtonValue(unittest.TestCase):
    def test_accepts_dict(self):
        self.assertEqual(parse_button_value({"a": 1}), {"a": 1})

    def test_accepts_json_string(self):
        self.assertEqual(parse_button_value('{"a": 1}'), {"a": 1})


if __name__ == "__main__":
    unittest.main()
