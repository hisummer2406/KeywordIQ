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
            # 先发送、发送成功才标记已恢复——`_send` 吞掉飞书 API 失败，若像旧实现
            # 那样先落库再发送，一次发送失败就会让这条告警永久卡在"服务端已判定
            # 恢复、飞书那边却从没收到过通知"的状态：下一轮巡检如果确实还是恢复的，
            # `is_alerting` 仍为 False 但 `was_firing` 已经因为上一轮的
            # `mark_alert_resolved` 变成 False，不会再重试发送这条消息
            # （PR #99 复审第三轮 P2）。发送失败就保持原状不落库：如果下一轮确认
            # 还是恢复状态会再次进入这个分支重试；如果那时其实又变回异常，
            # `is_alerting` 会重新为 True，走下面的告警分支。
            if await _send(feishu_client, chat_id, f"✅ 已恢复：{message}"):
                await crud.mark_alert_resolved(session, alert_key)
        return

    effective_level = "warn" if in_maintenance else level
    new_count = (state.consecutive_count if state else 0) + 1
    maintenance_note = "（发布/重启维护窗口内的预期现象，未按正常等级升级）" if in_maintenance else ""

    if not was_firing:
        prefix = "🚨" if effective_level == "crit" else "⚠️"
        # 同上：只有真的发出去才落 `last_fired_at`。旧实现先落库再发送，一次发送
        # 失败也会把 `last_fired_at` 更新成"现在"，下一轮巡检据此认为静默窗口刚刚
        # 开始，明明一条通知都没送达却要等一整个 `silence_minutes` 才会重试
        # （PR #99 复审第三轮 P2）。这里发送失败时不落任何库——本来就没有旧状态
        # 需要保留，下一轮会把它当"仍是第一次触发"原样重试。
        if await _send(feishu_client, chat_id, f"{prefix} {message}{maintenance_note}"):
            await crud.upsert_alert_fired(session, alert_key, effective_level, new_count)
        return

    silence_elapsed = (
        state.last_fired_at is not None
        and (datetime.now(UTC) - state.last_fired_at).total_seconds() >= silence_minutes * 60
    )
    just_escalated = new_count == escalate_after

    if silence_elapsed or just_escalated:
        if just_escalated and not silence_elapsed:
            text = f"🚨🚨 持续异常：{message}{maintenance_note}"
        else:
            prefix = "🚨" if effective_level == "crit" else "⚠️"
            text = f"{prefix} {message}{maintenance_note}"
        if await _send(feishu_client, chat_id, text):
            await crud.upsert_alert_fired(session, alert_key, effective_level, new_count)
            return
        # 发送失败：不落 `last_fired_at`（下一轮巡检据此立即重试，而不是进入
        # 一整个静默窗口），但仍推进 `consecutive_count`——它已存在的行不受
        # `bump_alert_consecutive_count` 的 WHERE 影响，升级计数不该因为一次
        # 发送失败就停摆（PR #99 复审第三轮 P2）。
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


async def _send(feishu_client: FeishuClient, chat_id: str, text: str) -> bool:
    """返回是否发送成功——调用方据此决定要不要把这次触发/恢复落库
    （PR #99 复审第三轮 P2，见 `evaluate` 里的两处调用点）。
    """
    try:
        await feishu_client.send_text(chat_id, text)
    except FeishuAPIError:
        logger.exception("发送告警消息失败：%s", text)
        return False
    return True
