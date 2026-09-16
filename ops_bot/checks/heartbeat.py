"""quick 分组的心跳任务：只证明"调度循环仍在推进"，不做任何实质检查。

`/ops/health` 读取这个 `kind=HEARTBEAT` 任务最近一次成功的 `finished_at`，据此
区分"进程能响应 HTTP 但事件循环已经卡死"与"一切正常"——两者在只探测 HTTP 层时
是同一个 200，见 design.md D4。
"""

from __future__ import annotations

KIND = "HEARTBEAT"


async def run() -> str:
    return "alive"
