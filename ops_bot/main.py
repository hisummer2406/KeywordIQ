"""ops-bot 的 FastAPI 入口：只暴露飞书 webhook 与自身健康检查两个端点。

不是业务 `app` 的一部分——host 级 systemd 服务（`python -m ops_bot.main`，
`etc/keywordiq-ops-bot.service`），见
openspec/changes/add-feishu-ops-bot/design.md D1。本文件当前只接入事件订阅的
URL 校验挑战、来源校验、幂等去重与身份白名单这条底座；消息/卡片的具体业务分发
（巡检查询、确认卡片回调）随后续任务组逐步接入，不在这次改动范围内。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, Request, Response

from config import settings
from ops_bot import crud, scheduler
from ops_bot.checks import heartbeat
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.auth import FeishuVerificationError, decode_event_body, is_authorized
from ops_bot.feishu.client import FeishuAPIError, FeishuClient

logger = logging.getLogger(__name__)

_feishu_client = FeishuClient(settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)

# 心跳分组间隔（quick，design.md D3）与 /ops/health 判定"仍在跳动"的容忍窗口。
# 90 秒 = 60 秒间隔 + 30 秒余量，给一次巡检偶发变慢留缓冲，同时仍远小于外部拨测的
# 检测周期，不会让"循环刚好卡在临界点"变成常态误报。
_HEARTBEAT_INTERVAL_SECONDS = 60
_HEARTBEAT_STALE_AFTER_SECONDS = 90


@asynccontextmanager
async def _lifespan(_: FastAPI):
    jobs = [
        scheduler.ScheduledJob(
            kind=heartbeat.KIND, interval_seconds=_HEARTBEAT_INTERVAL_SECONDS, run=heartbeat.run
        ),
    ]
    tasks = scheduler.start(jobs)
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
    """
    async with OpsAsyncSessionFactory() as session:
        last = await crud.last_succeeded_at(session, heartbeat.KIND)

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

    event_id = (event.get("header") or {}).get("event_id")
    if event_id:
        async with OpsAsyncSessionFactory() as session:
            is_new = await crud.try_claim_dedup_key(session, event_id)
            await session.commit()
        if not is_new:
            # 飞书重放：已处理过的事件直接 200，不重新入队。
            return Response(status_code=200)

    sender = ((event.get("event") or {}).get("sender") or {}).get("sender_id") or {}
    chat = (event.get("event") or {}).get("message") or {}
    if not is_authorized(
        open_id=sender.get("open_id"),
        chat_id=chat.get("chat_id"),
        owner_open_id=settings.FEISHU_OWNER_OPEN_ID,
        ops_chat_id=settings.FEISHU_OPS_CHAT_ID,
    ):
        logger.info("飞书事件来自非授权用户/会话，拒绝处理")
        return Response(status_code=200)

    # 具体的查询/指令分发（checks/executor）在后续任务组接入；这里先回一条确认收到的
    # 文本消息，让"回调及时应答、后台执行、再回报结果"这条协议在最简形态下就能验证通路
    # （design.md D1）。发送失败不影响 webhook 本身的 200 响应——飞书重试的是回调，
    # 不是我们主动发消息这一步，两者失败模式要分开，见 design.md 风险段"通知失败与
    # 执行失败分开记录"。
    logger.info("收到已授权的飞书事件，event_id=%s", event_id)
    try:
        await _feishu_client.send_text(settings.FEISHU_OPS_CHAT_ID, "已收到，功能开发中")
    except FeishuAPIError:
        logger.exception("回复确认消息失败")
    return Response(status_code=200)


def _json_response(payload: dict) -> Response:
    import json

    return Response(content=json.dumps(payload), media_type="application/json")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8101)
