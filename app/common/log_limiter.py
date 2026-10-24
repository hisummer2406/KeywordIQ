"""按窗口去重的诊断提示（#154 request-diagnostics-logging「分析查询诊断可分视图统计且提示有界去重」，design D6）。

拥堵时同一类保护性拒绝（排队超时、执行预算到点）会逐请求出现；每条都记会让日志量随拥堵
放大，而真正要知道的是「发生了、频率多高」。每个键在窗口内只放行第一条，其余计数；下一次
放行时把上个窗口省略的次数一并报出。每个请求的结局另有请求摘要逐条记录，次数不会丢。

不用后台定时器补发汇总：窗口结束后若同类不再出现，省略次数只存在于请求摘要里——为一条
汇总常驻一个任务不值得。键的数量有上限，超出时淘汰最久未出现的键（只丢它的省略计数）。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable


class WindowedLogLimiter:
    def __init__(
        self,
        window_seconds: float = 60.0,
        max_keys: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        # 键 → [窗口起点, 窗口内已省略次数]；按最近一次放行排序，便于淘汰最久的
        self._entries: OrderedDict[Hashable, list[float | int]] = OrderedDict()
        # 处理器在事件循环上，同步端点在线程池里；锁的代价可以忽略
        self._lock = threading.Lock()

    def admit(self, key: Hashable) -> int | None:
        """``None``：本条省略。整数：本条照常记录，值为上个窗口内省略的次数。"""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] < self._window:
                entry[1] += 1
                return None
            suppressed = int(entry[1]) if entry is not None else 0
            self._entries[key] = [now, 0]
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_keys:
                self._entries.popitem(last=False)
            return suppressed


def suppressed_note(suppressed: int) -> str:
    """拼在日志末尾的省略说明；没有省略时为空。"""
    return f"（上个窗口内另有 {suppressed} 次同类已省略）" if suppressed else ""


#: 进程内共用的一份：同类提示无论从哪里发出都按同一个窗口计数。
diagnostic_limiter = WindowedLogLimiter()
