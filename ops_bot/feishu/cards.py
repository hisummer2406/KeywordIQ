"""飞书互动卡片模板拼装：状态摘要、确认卡片、结果卡片。

**强类型入参，不接受自由字符串拼接**——与 `ops_bot/executor` 同一条纪律
（design.md D1）。按钮回传值用 JSON 编码结构化字段（`task_id`/`confirm_token`/
`decision`），回调侧按字段解析，不靠拼接/正则从一段自由文本里抠数据。

卡片 JSON 结构遵循飞书经典卡片格式（`config`/`header`/`elements`），这是本变更
依据官方文档做的判断，**未经真实飞书客户端渲染验证**——tasks.md 3.8/演练阶段
要求用真实应用实测一次，核对渲染效果与字段是否被正确识别。
"""

from __future__ import annotations

import json


def _button_value(**fields) -> dict:
    return dict(fields)


def build_status_card(*, title: str, lines: list[str]) -> dict:
    """一屏状态摘要：服务/资源/导入/备份/未解决告警。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
    }


def build_confirm_card(
    *,
    action: str,
    task_id: str,
    confirm_token: str,
    summary_lines: list[str],
    template: str = "orange",
) -> dict:
    """重启/发布通用确认卡片。两个按钮携带同一个 task_id/confirm_token，
    `decision` 区分确认/取消——执行器只认这个字段，不解析按钮文案。
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"{action} 待确认"}, "template": template},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(summary_lines)}},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认执行"},
                        "type": "primary",
                        "value": _button_value(task_id=task_id, confirm_token=confirm_token, decision="confirm"),
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "取消"},
                        "type": "default",
                        "value": _button_value(task_id=task_id, confirm_token=confirm_token, decision="cancel"),
                    },
                ],
            },
        ],
    }


def build_result_card(*, action: str, success: bool, output_excerpt: str) -> dict:
    """任务完成后原地更新的结果卡片（配合 `FeishuClient.update_card`）。"""
    template = "green" if success else "red"
    status_text = "成功" if success else "失败"
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"{action} 已{status_text}"}, "template": template},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"```\n{output_excerpt}\n```"}},
        ],
    }


def parse_button_value(raw_value: dict | str) -> dict:
    """回调携带的 value 可能已被飞书解析成 dict，也可能是原始 JSON 字符串——两种都接受。"""
    if isinstance(raw_value, dict):
        return raw_value
    return json.loads(raw_value)
