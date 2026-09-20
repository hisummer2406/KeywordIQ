"""每日巡检摘要（design.md D3，每日北京时间 09:00）。

内容组装与发送分离：`build_lines()` 是纯函数（好测），`send()` 只负责拉数据、
调用它、发卡片。
"""

from __future__ import annotations

import logging

from ops_bot import crud
from ops_bot.checks import resources, service
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.cards import build_status_card
from ops_bot.feishu.client import FeishuAPIError, FeishuClient

logger = logging.getLogger(__name__)


def build_lines(
    *,
    app_healthy: bool,
    disk_percent: float,
    unresolved_alert_keys: list[str],
) -> list[str]:
    lines = [
        f"服务：{'✅ 正常' if app_healthy else '❌ 不可用'}",
        f"磁盘：{disk_percent:.1f}%",
    ]
    if unresolved_alert_keys:
        lines.append(f"未解决告警（{len(unresolved_alert_keys)}）：{', '.join(unresolved_alert_keys)}")
    else:
        lines.append("未解决告警：无")
    return lines


async def send(feishu_client: FeishuClient, chat_id: str) -> str:
    app_healthy = await service.is_app_healthy()
    disk_percent = resources.disk_usage_percent()

    async with OpsAsyncSessionFactory() as session:
        unresolved = await crud.list_unresolved_alerts(session)

    lines = build_lines(
        app_healthy=app_healthy,
        disk_percent=disk_percent,
        unresolved_alert_keys=[a.alert_key for a in unresolved],
    )
    card = build_status_card(title="每日巡检摘要", lines=lines)
    output = "\n".join(lines)

    # 通知失败与执行失败分开记录（design.md「通知失败与执行失败分开记录」）：
    # 摘要本身已经算出来了，这件事没有失败；失败的只是"发出去"这一步，不该让
    # 这次调度被记成 FAILED——那会让"今天的摘要算失败了"与"算出来了但没发出去"
    # 在 ops_task 里长得一模一样，排查时无从区分。发送失败改记进 output，
    # 状态仍是 SUCCEEDED。
    try:
        await feishu_client.send_card(chat_id, card)
    except FeishuAPIError:
        logger.exception("发送每日巡检摘要卡片失败")
        output += "\n⚠️ 卡片发送失败，详见应用日志（摘要内容本身已算出，未受影响）"

    return output
