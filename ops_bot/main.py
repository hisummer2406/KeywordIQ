"""ops-bot 的 FastAPI 入口：只暴露飞书 webhook 与自身健康检查两个端点。

不是业务 `app` 的一部分——独立进程/容器（`python -m ops_bot.main`），见
openspec/changes/add-feishu-ops-bot/design.md D1。本文件当前只接入事件订阅的
URL 校验挑战、来源校验、幂等去重与身份白名单这条底座；消息/卡片的具体业务分发
（巡检查询、确认卡片回调）随后续任务组逐步接入，不在这次改动范围内。
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI, Request, Response

from config import settings
from ops_bot import crud
from ops_bot.database import OpsAsyncSessionFactory
from ops_bot.feishu.auth import FeishuVerificationError, decode_event_body, is_authorized
from ops_bot.feishu.client import FeishuAPIError, FeishuClient

logger = logging.getLogger(__name__)

app = FastAPI(title="KeywordIQ ops-bot")
_feishu_client = FeishuClient(settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)


@app.get("/ops/health")
async def health() -> dict:
    """机器人自身心跳。当前恒返回健康——升级为"巡检循环是否仍在跳动"由调度任务组接入
    （design.md D4：探测的是循环是否推进，不只是进程能否响应 HTTP）。"""
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
