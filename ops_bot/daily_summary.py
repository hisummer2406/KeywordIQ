"""每日巡检摘要（design.md D3，每日北京时间 09:00）。

内容组装与发送分离：`build_lines()` 是纯函数（好测），`send()` 只负责拉数据、
调用它、发卡片。
"""

from __future__ import annotations

from ops_bot import backup, crud
from ops_bot.checks import resources, service
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.cards import build_status_card
from ops_bot.feishu.client import FeishuClient


def build_lines(
    *,
    app_healthy: bool,
    disk_percent: float,
    backup_age_hours: float | None,
    unresolved_alert_keys: list[str],
) -> list[str]:
    lines = [
        f"服务：{'✅ 正常' if app_healthy else '❌ 不可用'}",
        f"磁盘：{disk_percent:.1f}%",
    ]
    if backup_age_hours is None:
        lines.append("备份：⚠️ 从未成功过")
    else:
        lines.append(f"备份：距上次成功 {backup_age_hours:.1f} 小时前")
    if unresolved_alert_keys:
        lines.append(f"未解决告警（{len(unresolved_alert_keys)}）：{', '.join(unresolved_alert_keys)}")
    else:
        lines.append("未解决告警：无")
    return lines


async def send(feishu_client: FeishuClient, chat_id: str) -> str:
    app_healthy = await service.is_app_healthy()
    disk_percent = resources.disk_usage_percent()
    backup_age_hours = await backup.latest_backup_age_hours()

    async with OpsAsyncSessionFactory() as session:
        unresolved = await crud.list_unresolved_alerts(session)

    lines = build_lines(
        app_healthy=app_healthy,
        disk_percent=disk_percent,
        backup_age_hours=backup_age_hours,
        unresolved_alert_keys=[a.alert_key for a in unresolved],
    )
    card = build_status_card(title="每日巡检摘要", lines=lines)
    await feishu_client.send_card(chat_id, card)
    return "\n".join(lines)
