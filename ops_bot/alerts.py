"""告警去重、升级与维护窗口的状态机（design.md D3）。

每次巡检判据只调一次 `evaluate()`，由它决定这一轮是否要真的发飞书消息，并把
`ops_alert_state` 更新成与这次判定一致的状态。**不在这里判断"是否异常"**——
那是各 `checks/*` 模块的职责，`evaluate()` 只接收一个布尔 `is_alerting` 结论。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ops_bot import crud
from ops_bot.feishu.client import FeishuAPIError, FeishuClient

logger = logging.getLogger(__name__)

DEFAULT_ESCALATE_AFTER = 3


async def evaluate(
    session: AsyncSession,
    feishu_client: FeishuClient,
    chat_id: str,
    *,
    alert_key: str,
    level: str,
    is_alerting: bool,
    message: str,
    silence_minutes: int,
    escalate_after: int = DEFAULT_ESCALATE_AFTER,
) -> None:
    state = await crud.get_alert_state(session, alert_key)
    was_firing = state is not None and state.resolved_at is None and state.last_fired_at is not None
    in_maintenance = (
        state is not None
        and state.maintenance_until is not None
        and state.maintenance_until > datetime.now(UTC)
    )

    if not is_alerting:
        if was_firing:
            await crud.mark_alert_resolved(session, alert_key)
            await _send(feishu_client, chat_id, f"✅ 已恢复：{message}")
        return

    effective_level = "warn" if in_maintenance else level
    new_count = (state.consecutive_count if state else 0) + 1
    maintenance_note = "（发布/重启维护窗口内的预期现象，未按正常等级升级）" if in_maintenance else ""

    if not was_firing:
        await crud.upsert_alert_fired(session, alert_key, effective_level, new_count)
        prefix = "🚨" if effective_level == "crit" else "⚠️"
        await _send(feishu_client, chat_id, f"{prefix} {message}{maintenance_note}")
        return

    silence_elapsed = (
        state.last_fired_at is not None
        and (datetime.now(UTC) - state.last_fired_at).total_seconds() >= silence_minutes * 60
    )
    just_escalated = new_count == escalate_after

    if silence_elapsed or just_escalated:
        await crud.upsert_alert_fired(session, alert_key, effective_level, new_count)
        if just_escalated and not silence_elapsed:
            await _send(feishu_client, chat_id, f"🚨🚨 持续异常：{message}{maintenance_note}")
        else:
            prefix = "🚨" if effective_level == "crit" else "⚠️"
            await _send(feishu_client, chat_id, f"{prefix} {message}{maintenance_note}")
    else:
        await crud.bump_alert_consecutive_count(session, alert_key, new_count)


async def open_maintenance_window(
    session: AsyncSession, alert_keys: list[str], *, minutes: int
) -> None:
    """重启/发布执行开始时打开维护窗口——巡检据此把预期的短暂不可用降级为 warn，
    不按正常规则升级/刷屏（design.md D3）。
    """
    until = datetime.now(UTC) + timedelta(minutes=minutes)
    for key in alert_keys:
        await crud.set_alert_maintenance_until(session, key, until)


async def _send(feishu_client: FeishuClient, chat_id: str, text: str) -> None:
    try:
        await feishu_client.send_text(chat_id, text)
    except FeishuAPIError:
        logger.exception("发送告警消息失败：%s", text)
