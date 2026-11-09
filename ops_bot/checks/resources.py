"""宿主机资源水位。`ops-bot` 是 host 级服务，`psutil.disk_usage` 看到的就是真实宿主机
磁盘（不像容器化探测那样需要额外挂载 `/host/root` 才能避开容器 rootfs 的假数字，
见 `docker-compose.yml` netdata 服务那段注释里踩过的坑——host 级服务天然不受这个限制）。
"""

from __future__ import annotations

import psutil

DISK_WARN_PERCENT = 70.0
DISK_CRIT_PERCENT = 90.0
DISK_WARN_REMINDER_MINUTES = 24 * 60  # 70～90% 还有处理时间，每天提醒一次即可


def disk_usage_percent(path: str = "/") -> float:
    return psutil.disk_usage(path).percent
