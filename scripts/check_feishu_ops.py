"""飞书运维机器人（ops-bot）链路自检：把「配置填对了没」变成一条可执行的判据。

按 design.md D2 列出的四项依次验证，退出码非 0 即有项未通过：

1. `FEISHU_APP_ID`/`FEISHU_APP_SECRET` 能换到 `tenant_access_token`。
2. `FEISHU_OPS_CHAT_ID` 能收到一条测试消息（机器人需已被拉进该群）。
3. `FEISHU_OWNER_OPEN_ID` 格式合法（`ou_` 前缀）。
4. `OPS_DATABASE_URL` 能连通，且权限边界确实是最小权限——尝试写一行 `ops_task`
   再删除（应成功）、尝试读 `analysis.import_batch` 一行（应成功）、尝试写
   `analysis.import_batch`（应被数据库拒绝）。**第三个子检查是反向验证**：证明
   "最小权限"不是配了但没生效，这类问题在其余三项检查全过之后才会第一次显形
   ——被攻破的账号在拿到写权限之前完全看不出区别。

用法::

    uv run python scripts/check_feishu_ops.py

只读检查（1-3）在 `FEISHU_*` 未配置时会直接报未通过，不静默跳过——一个"配了但
不生效"的自检脚本比没有更糟。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config import settings
from ops_bot.feishu.client import FeishuAPIError, FeishuClient

OK = "✅"
BAD = "❌"
INFO = "ℹ️ "


async def _check_tenant_token() -> str | None:
    print("\n== 1. 换取 tenant_access_token ==")
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        print(f"{BAD} FEISHU_APP_ID / FEISHU_APP_SECRET 未配置。")
        return None

    client = FeishuClient(settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)
    try:
        token = await client.tenant_access_token()
    except FeishuAPIError as exc:
        print(f"{BAD} 换取失败：{exc}")
        print("     常见成因：App ID/Secret 填错，或应用未发布/未启用机器人能力。")
        return None
    print(f"{OK} 拿到 tenant_access_token（{len(token)} 字符）")
    return token if isinstance(token, str) else None


async def _check_send_test_message() -> bool:
    print("\n== 2. 发送测试消息到运维群 ==")
    if not settings.FEISHU_OPS_CHAT_ID:
        print(f"{BAD} FEISHU_OPS_CHAT_ID 未配置。")
        return False

    client = FeishuClient(settings.FEISHU_APP_ID, settings.FEISHU_APP_SECRET)
    try:
        await client.send_text(settings.FEISHU_OPS_CHAT_ID, "ops-bot 自检：这是一条测试消息")
    except FeishuAPIError as exc:
        print(f"{BAD} 发送失败：{exc}")
        print("     常见成因：chat_id 填错，或机器人未被拉进这个群。")
        return False
    print(f"{OK} 已发送，请到运维群核对是否收到。")
    return True


def _check_owner_open_id_format() -> bool:
    print("\n== 3. 校验 FEISHU_OWNER_OPEN_ID 格式 ==")
    value = settings.FEISHU_OWNER_OPEN_ID
    if not value:
        print(f"{BAD} FEISHU_OWNER_OPEN_ID 未配置。")
        return False
    if not value.startswith("ou_"):
        print(f"{BAD} {value} 不是 ou_ 开头——这不是合法的飞书 open_id 形态，"
              "可能填成了 union_id 或 user_id。")
        return False
    print(f"{OK} {value}")
    return True


async def _check_database_boundary() -> bool:
    print("\n== 4. 数据库最小权限边界 ==")
    if not settings.OPS_DATABASE_URL_ASYNC:
        print(f"{BAD} OPS_DATABASE_URL_ASYNC 未配置。")
        return False

    engine = create_async_engine(settings.OPS_DATABASE_URL_ASYNC)

    # **先单独确认连得上库，连不上就直接返回，不再往下跑三项子检查。**
    # 少了这一步，第三项"反向验证写 analysis.import_batch 应被拒绝"会把"连接失败"
    # 误判成"权限边界生效"——两者都会走进 except 分支，但只有真正连上库、执行到
    # 数据库拒绝写权限那一刻，这条反向验证才有意义（本地无 ops_bot_role 账号时
    # 实测踩中过这个假阳性：密码错误也被打印成了"符合预期"）。
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"{BAD} 连接 OPS_DATABASE_URL_ASYNC 失败，后续三项子检查跳过：{exc}")
        await engine.dispose()
        return False

    ok = True
    # 三个子检查各用自己的连接/事务——一条连接在语句失败后会进入"事务已中止"状态，
    # 必须靠退出 `begin()` 时的自动回滚清干净，混用同一个连接给后续步骤会平白多一层
    # "这个失败是不是被前一步的中止事务污染了"的排查负担，不值得为了省一个连接对象。
    probe_id = "check-feishu-ops-probe"
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ops.ops_task (id, kind, status) "
                    "VALUES (:id, 'SELF_CHECK', 'SUCCEEDED') ON CONFLICT (id) DO NOTHING"
                ),
                {"id": probe_id},
            )
            await conn.execute(text("DELETE FROM ops.ops_task WHERE id = :id"), {"id": probe_id})
        print(f"{OK} 对 ops.ops_task 读写正常。")
    except Exception as exc:
        print(f"{BAD} 连接或对 ops.ops_task 读写失败：{exc}")
        ok = False

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT id FROM analysis.import_batch LIMIT 1"))
        print(f"{OK} 对 analysis.import_batch 只读正常。")
    except Exception as exc:
        print(f"{BAD} 读 analysis.import_batch 失败：{exc}")
        ok = False

    # 反向验证：写 analysis.import_batch 必须被数据库拒绝，否则"最小权限"只是
    # 文档承诺，不是真实生效的边界。
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM analysis.import_batch WHERE 1=0"))
        print(f"{BAD} 竟然能对 analysis.import_batch 执行写操作——"
              "ops_bot_role 的权限授予有误，请核对迁移是否完整执行。")
        ok = False
    except Exception:
        print(f"{OK} 对 analysis.import_batch 的写操作被数据库拒绝（符合预期）。")

    await engine.dispose()
    return ok


async def _run() -> bool:
    token_ok = await _check_tenant_token() is not None
    message_ok = await _check_send_test_message() if token_ok else False
    owner_ok = _check_owner_open_id_format()
    db_ok = await _check_database_boundary()
    return token_ok and message_ok and owner_ok and db_ok


def main() -> int:
    ok = asyncio.run(_run())
    if ok:
        print(f"\n{OK} 整条链路通过。")
        return 0
    print(f"\n{BAD} 有检查未通过，见上面的提示。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
