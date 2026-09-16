"""飞书消息里的文本指令分发：状态查询、任务查询、日志查询。

**只读查询，不含任何受限动作**——重启/发布走卡片确认流程（第 6 组），不经这条
文本指令路径。每条指令处理函数只负责"查什么、怎么拼文本"，发送本身由调用方
（`ops_bot/main.py`）统一处理，方便未来把"发送失败"的日志一处收拢。
"""

from __future__ import annotations

from ops_bot import backup, crud
from ops_bot.checks import logs, resources, service
from ops_bot.database import OpsAsyncSessionFactory

_LOG_PATH = "logs/app.log"

_HELP_TEXT = (
    "可用指令：\n"
    "「状态」查看服务/资源/备份摘要\n"
    "「任务」查看最近运维任务\n"
    "「日志 <request_id>」按请求编号查日志\n"
    "「备份记录」查看最近一次成功备份的详情\n"
    "「备份」立即触发一次手动备份"
)


async def dispatch(text: str) -> str:
    """返回要发给用户的文本；不抛业务异常（未知指令返回帮助文案，不是错误）。

    按空白切成"指令头 + 剩余参数"两段，不用 `startswith("日志 ")` 这类固定前缀
    匹配——"日志 "（尾随空格、无参数）会被 `.strip()` 削掉那个空格，`startswith`
    判断就失配，指令被误判成"不认识"退回帮助文案，而不是"缺参数"提示。
    """
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return _HELP_TEXT
    head, rest = parts[0], (parts[1] if len(parts) > 1 else "")

    if head in ("状态", "status"):
        return await _status()
    if head in ("任务", "task", "tasks"):
        return await _recent_tasks()
    if head in ("日志", "log"):
        return _find_logs(rest.strip())
    if head in ("备份记录", "backups"):
        return await _latest_backup()

    return _HELP_TEXT


async def _status() -> str:
    healthy = await service.is_app_healthy()
    disk_percent = resources.disk_usage_percent()
    backup_age_hours = await backup.latest_backup_age_hours()

    lines = [
        f"服务：{'✅ 正常' if healthy else '❌ 不可用'}",
        f"磁盘：{disk_percent:.1f}%",
    ]
    if backup_age_hours is None:
        lines.append("备份：⚠️ 从未成功过")
    else:
        lines.append(f"备份：距上次成功 {backup_age_hours:.1f} 小时前")
    return "\n".join(lines)


async def _recent_tasks() -> str:
    async with OpsAsyncSessionFactory() as session:
        tasks = await crud.recent_tasks(session, limit=10)
    if not tasks:
        return "暂无运维任务记录"
    lines = [f"{t.created_at:%m-%d %H:%M} {t.kind} {t.status}" for t in tasks]
    return "\n".join(lines)


async def _latest_backup() -> str:
    async with OpsAsyncSessionFactory() as session:
        record = await crud.latest_succeeded_backup(session)
    if record is None:
        return "从未成功备份过"

    lines = [f"时间：{record.finished_at:%Y-%m-%d %H:%M}", f"产物：{record.object_key}"]
    if record.size_bytes:
        lines.append(f"大小：{record.size_bytes / 1024 / 1024:.1f} MB")
    return "\n".join(lines)


def _find_logs(request_id: str) -> str:
    if not request_id:
        return "用法：日志 <request_id>"
    matched = logs.find_by_request_id(_LOG_PATH, request_id)
    if not matched:
        return f"未找到 request_id={request_id} 的日志（仅检索日志文件末尾的近期部分）"
    return "\n".join(matched)
