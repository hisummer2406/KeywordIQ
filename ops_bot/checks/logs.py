"""有界的日志读取：近期错误计数、按 request_id 反查。

`ops-bot` 是 host 级服务（design.md D1 修订），与 `logs/app.log` 同处一台宿主机，
直接读文件即可，不需要跨容器/跨网络的日志采集管道。日志格式来自 `main.py::
configure_logging` 的 `logging.basicConfig` 与请求摘要中间件（`app/common/
request_logging.py`）的固定拼接方式，本模块按这两处**当前**的字符串格式解析——
两边任一格式改动，这里的正则要跟着改，不存在结构化日志的双向契约保证这一点。

**有界**体现在两处：只读文件末尾 `_TAIL_BYTES` 字节（不整文件载入内存），
`find_by_request_id` 的返回条数与时间窗口都由调用方给上限。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<name>\S+) - "
    r"\[(?P<request_id>[^\]]*)\] - pid=(?P<pid>\d+) - (?P<level>\w+) - (?P<message>.*)$"
)
_5XX_RE = re.compile(r"status=5\d\d")
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})

_TAIL_BYTES = 8 * 1024 * 1024  # 8MB，覆盖日报导入量级下一天的日志量仍绰绰有余


def _read_tail_lines(log_path: str) -> list[str]:
    path = Path(log_path)
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > _TAIL_BYTES:
            f.seek(size - _TAIL_BYTES)
            f.readline()  # 丢弃可能被截断的首行
        raw = f.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")
    except ValueError:
        return None


def count_recent_errors(log_path: str, *, since_minutes: int) -> int:
    """近 `since_minutes` 分钟内的 ERROR/CRITICAL 级日志 + 5xx 请求摘要计数。

    两类来源合并去重（同一行两个判据都命中只算一次）——业务错误常以
    `outcome=completed status=5xx` 记在 INFO 级（`request_logging.py`
    的正常完成分支不区分状态码），只看 level 会漏掉这类；只看 status=5xx
    又会漏掉未包出请求摘要的其它 ERROR 日志（如后台任务异常）。
    """
    cutoff = datetime.now() - timedelta(minutes=since_minutes)
    count = 0
    for line in _read_tail_lines(log_path):
        match = _LINE_RE.match(line)
        if not match:
            continue
        ts = _parse_ts(match.group("ts"))
        if ts is None or ts < cutoff:
            continue
        if match.group("level") in _ERROR_LEVELS or _5XX_RE.search(match.group("message")):
            count += 1
    return count


def find_by_request_id(log_path: str, request_id: str, *, max_lines: int = 50) -> list[str]:
    """按 request_id 反查关联日志行，最多返回 `max_lines` 条。

    不脱敏字段内容本身——脱敏契约在写入日志那一刻就已经生效（业务侧只白名单
    字段落日志，见 add-request-diagnostics-logging 变更），这里只做检索，
    不重新校验或过滤字段。
    """
    matched = [
        line
        for line in _read_tail_lines(log_path)
        if (m := _LINE_RE.match(line)) and m.group("request_id") == request_id
    ]
    return matched[-max_lines:]
