"""ops-bot 的 FastAPI 入口：只暴露飞书 webhook 与自身健康检查两个端点。

不是业务 `app` 的一部分——host 级 systemd 服务（`python -m ops_bot.main`，
`etc/keywordiq-ops-bot.service`），见
openspec/changes/add-feishu-ops-bot/design.md D1。本文件当前只接入事件订阅的
URL 校验挑战、来源校验、幂等去重与身份白名单这条底座；消息/卡片的具体业务分发
（巡检查询、确认卡片回调）随后续任务组逐步接入，不在这次改动范围内。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, Request, Response

from config import settings
from ops_bot import backup, commands, crud, jobs, scheduler
from ops_bot.checks import heartbeat
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu import cards
from ops_bot.feishu.auth import FeishuVerificationError, decode_event_body, is_authorized
from ops_bot.feishu.client import FeishuAPIError, FeishuClient
from ops_bot.release import flow as release_flow

_MANUAL_BACKUP_WORDS = frozenset({"备份", "backup"})
_RESTART_WORDS = frozenset({"重启", "restart"})
_DEPLOY_WORDS = frozenset({"发布", "deploy"})

# asyncio 文档明确要求：create_task 的返回值若不被任何地方持有，任务可能在
# 执行期间被垃圾回收而静默中断。这个集合就是那个"持有者"，任务结束时自己从
# 集合里摘除。
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

logger = logging.getLogger(__name__)

_feishu_client = FeishuClient(settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)

# 心跳分组间隔（quick，design.md D3）与 /ops/health 判定"仍在跳动"的容忍窗口。
# 90 秒 = 60 秒间隔 + 30 秒余量，给一次巡检偶发变慢留缓冲，同时仍远小于外部拨测的
# 检测周期，不会让"循环刚好卡在临界点"变成常态误报。
_HEARTBEAT_INTERVAL_SECONDS = 60
_HEARTBEAT_STALE_AFTER_SECONDS = 90


@asynccontextmanager
async def _lifespan(_: FastAPI):
    tasks = scheduler.start(jobs.build_jobs(_feishu_client, settings.FEISHU_OPS_CHAT_ID))
    try:
        yield
    finally:
        await scheduler.stop(tasks)


app = FastAPI(title="KeywordIQ ops-bot", lifespan=_lifespan)


@app.get("/ops/health")
async def health(response: Response) -> dict:
    """机器人自身心跳：读取 quick 分组心跳任务最近一次成功时间，超过容忍窗口未更新
    判定为"事件循环已卡死"，返回 503——探测的是循环是否仍在推进，不只是 HTTP
    层能否响应（design.md D4，用于区分"整机失联"与"进程活着但卡死"两种失联）。

    `ops` schema 与业务 `analysis` schema 同处一个自建 PostgreSQL 实例（design.md
    D1），数据库连接本身失败时不能让这个端点裸抛 500——那样探测方只看到一次
    不成形的错误，而不是"数据库不可用"这条明确诊断（design.md「业务应用/数据库
    异常时仍须可报告健康失败」，虽然字面说的是业务库，但同一实例整体不可达时
    ops-bot 自己的查询同样会失败，需要同一条纪律兜底）。
    """
    try:
        async with OpsAsyncSessionFactory() as session:
            last = await crud.last_succeeded_at(session, heartbeat.KIND)
    except Exception:
        logger.exception("/ops/health 查询心跳记录失败，数据库可能不可达")
        response.status_code = 503
        return {"status": "db_unreachable"}

    if last is None:
        # 服务刚启动，第一次心跳还没跑完——不算异常，quick 循环最迟 60 秒内会补上。
        return {"status": "starting"}

    age_seconds = (datetime.now(UTC) - last).total_seconds()
    if age_seconds > _HEARTBEAT_STALE_AFTER_SECONDS:
        response.status_code = 503
        return {"status": "stale", "last_heartbeat_seconds_ago": age_seconds}

    return {"status": "ok"}


@app.post("/ops/feishu/webhook")
async def feishu_webhook(request: Request) -> Response:
    raw_body = await request.body()

    try:
        event = decode_event_body(
            raw_body,
            verification_token=settings.FEISHU_VERIFICATION_TOKEN,
            encrypt_key=settings.FEISHU_ENCRYPT_KEY,
        )
    except FeishuVerificationError:
        logger.warning("飞书 webhook 来源校验失败")
        return Response(status_code=403)

    # URL 校验挑战：飞书首次配置事件订阅时发送，先于任何鉴权/幂等处理，原样回显 challenge。
    if event.get("type") == "url_verification":
        return _json_response({"challenge": event.get("challenge")})

    # 卡片按钮回调：飞书经典卡片回传交互的请求体在顶层带 `action` 字段，与
    # `im.message.receive_v1` 事件（顶层是 `header`/`event` 包裹）结构不同，
    # 走独立分支处理。**这条路径依据飞书官方文档实现，未经真实客户端回调验证**
    # ——tasks.md 已标注为生产/飞书实机待验证项（design.md D5/`feishu/cards.py`
    # 同一处标注）。
    if "action" in event:
        return await _handle_card_action(event)

    event_id = (event.get("header") or {}).get("event_id")
    if event_id:
        async with OpsAsyncSessionFactory() as session:
            is_new = await crud.try_claim_dedup_key(session, event_id)
            await session.commit()
        if not is_new:
            # 飞书重放：已处理过的事件直接 200，不重新入队。
            return Response(status_code=200)

    sender = ((event.get("event") or {}).get("sender") or {}).get("sender_id") or {}
    message = (event.get("event") or {}).get("message") or {}
    if not is_authorized(
        open_id=sender.get("open_id"),
        chat_id=message.get("chat_id"),
        owner_open_id=settings.FEISHU_OWNER_OPEN_ID,
        ops_chat_id=settings.FEISHU_OPS_CHAT_ID,
    ):
        logger.info("飞书事件来自非授权用户/会话，拒绝处理")
        return Response(status_code=200)

    logger.info("收到已授权的飞书事件，event_id=%s", event_id)
    text = _extract_text(message.get("content", "{}")).strip()
    open_id = sender.get("open_id")

    # 整段指令分发包一层兜底：`ops` schema 与业务 `analysis` schema 同处一个自建
    # PostgreSQL 实例（design.md D1），数据库不可达时这里任何一条查询都可能抛出
    # 未预期的异常。没有这层兜底，异常会从这个端点裸抛出去变成 500——事件已经在
    # 上面标记过"已处理"（dedup key 已落库），飞书不会重投，用户会发现自己的指令
    # 得不到任何回应且无从排查。兜底后至少能告诉用户"失败了"，详细原因留给日志
    # （design.md「业务应用/数据库异常时仍须可报告健康失败」，此处适用于 ops-bot
    # 自己与业务共享的数据库实例整体不可达的情形）。
    try:
        # 手动备份是本变更唯一一个"不需要确认卡片就能直接创建任务"的写操作
        # （design.md D5 只要求重启/发布走确认卡片，备份未被点名，且备份本身可
        # 重复、不影响正在提供服务的应用，风险量级与前两者不同）。备份要跑几分钟，
        # 不能让 webhook 等它跑完才回 200——先回执行中，任务本身放到后台跑，完成
        # 后再单独发一条结果消息，这与飞书回调"及时应答、后台执行、再回报结果"的
        # 协议一致。
        if text in _MANUAL_BACKUP_WORDS:
            _spawn_background(_run_manual_backup(open_id))
            await _reply("已受理备份任务，执行中…")
            return Response(status_code=200)

        # 重启/发布：生成确认卡片，不直接执行——人工点击卡片按钮才会真正触发
        # （design.md D5，走 _handle_card_action）。
        head, _, rest = text.partition(" ")
        if head in _RESTART_WORDS:
            plan, reason = await release_flow.build_restart_plan(open_id)
            await _send_plan_or_reason(plan, reason)
            return Response(status_code=200)
        if head in _DEPLOY_WORDS:
            target_sha = rest.strip()
            if not target_sha:
                await _reply("用法：发布 <完整 40 位 commit SHA>")
                return Response(status_code=200)
            plan, reason = await release_flow.build_deploy_plan(target_sha, open_id)
            await _send_plan_or_reason(plan, reason)
            return Response(status_code=200)

        # 其余只读指令分发（状态/任务/日志查询）。
        reply = await commands.dispatch(text)
    except Exception:
        logger.exception("处理飞书指令时发生未预期异常，text=%r", text)
        await _reply("❌ 处理失败，详见应用日志（可能是数据库暂时不可达）")
        return Response(status_code=200)

    # 回复失败不影响 webhook 本身的 200 响应：飞书重试的是回调本身，不是我们主动
    # 发消息这一步，两者失败模式要分开（design.md「通知失败与执行失败分开记录」）。
    await _reply(reply)
    return Response(status_code=200)


async def _handle_card_action(event: dict) -> Response:
    """重启/发布确认卡片的按钮回调。见 `feishu_webhook` 上方的格式说明与
    `feishu/cards.py` 的同一条"未经真实客户端验证"标注。
    """
    open_id = event.get("open_id")
    chat_id = event.get("open_chat_id")
    if not is_authorized(
        open_id=open_id,
        chat_id=chat_id,
        owner_open_id=settings.FEISHU_OWNER_OPEN_ID,
        ops_chat_id=settings.FEISHU_OPS_CHAT_ID,
    ):
        logger.info("卡片回调来自非授权用户/会话，拒绝处理")
        return Response(status_code=200)

    try:
        value = cards.parse_button_value((event.get("action") or {}).get("value") or {})
    except (ValueError, TypeError):
        logger.warning("卡片回调 value 解析失败")
        return Response(status_code=200)

    task_id, confirm_token, decision = value.get("task_id"), value.get("confirm_token"), value.get("decision")
    if not (task_id and confirm_token and decision):
        return Response(status_code=200)

    outcome = await release_flow.handle_confirm_callback(task_id, confirm_token, decision)
    if outcome.run is not None:
        # 真正的重启/发布可能跑到 executor 的 1800 秒超时——不能在这个回调请求
        # 协程里等它跑完，否则会同时超过飞书的重试窗口与 nginx 反代超时，飞书
        # 只看到超时重试而收不到任何响应（PR #99 Codex 复审 P1）。先受理、
        # 后台执行、完成后再单独发一条结果消息，与手动备份走的是同一个协议
        # （design.md「飞书回调及时应答，长任务后台执行」）。
        run = outcome.run

        async def _run_and_reply() -> None:
            result = await run()
            await _reply(result)

        _spawn_background(_run_and_reply())
        await _reply("已受理，执行中…")
    else:
        await _reply(outcome.immediate_reply)
    return Response(status_code=200)


async def _send_plan_or_reason(plan, reason: str | None) -> None:
    if plan is None:
        await _reply(f"❌ {reason}")
        return
    try:
        await _feishu_client.send_card(settings.FEISHU_OPS_CHAT_ID, plan.card)
    except FeishuAPIError:
        logger.exception("发送确认卡片失败")


async def _reply(text: str) -> None:
    try:
        await _feishu_client.send_text(settings.FEISHU_OPS_CHAT_ID, text)
    except FeishuAPIError:
        logger.exception("回复消息失败")


async def _run_manual_backup(requested_by_open_id: str | None) -> None:
    async with OpsAsyncSessionFactory() as session:
        task_id = await crud.create_task(
            session, kind="BACKUP", requested_by_open_id=requested_by_open_id
        )
        await session.commit()

    try:
        output = await backup.run_backup(
            retention_days=settings.OPS_BACKUP_RETENTION_DAYS,
            task_id=task_id,
            actor_open_id=requested_by_open_id,
        )
    except Exception:
        logger.exception("手动备份失败")
        async with OpsAsyncSessionFactory() as session:
            await crud.mark_task_failed(session, task_id, output="执行异常，详见应用日志")
            await session.commit()
        reply = "❌ 手动备份失败，详见应用日志"
    else:
        async with OpsAsyncSessionFactory() as session:
            await crud.mark_task_succeeded(session, task_id, output=output)
            await session.commit()
        reply = f"✅ {output}"

    await _reply(reply)


def _extract_text(content_json: str) -> str:
    try:
        return json.loads(content_json).get("text", "")
    except (ValueError, TypeError):
        return ""


def _json_response(payload: dict) -> Response:
    import json

    return Response(content=json.dumps(payload), media_type="application/json")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8101)
