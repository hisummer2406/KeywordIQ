# monitoring.py
"""宿主机指标采样。

**全部是同步阻塞调用**（psutil 的采样要真的等一个采样窗口），所以这里刻意不写成
`async def`——写成 async 只会让调用方误以为它可以在事件循环上直接 await，而那正是
让整个 worker 停摆一整个采样窗口的写法。调用方负责把它放进线程池。
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import psutil

logger = logging.getLogger(__name__)

# 采样窗口。psutil 要在这段时间里对比两次 CPU 时间片才能算出占用率。
SAMPLE_INTERVAL_SECONDS = 1
# 两次真实采样之间的最小间隔。窗口内的请求直接复用上一份结果。
#
# 每次采样都占住一条 anyio 线程池线程整整 SAMPLE_INTERVAL_SECONDS，而那个线程池
# （THREAD_POOL_LIMIT=32）是与 bcrypt 登录、CSV 导出、同步导入任务共用的。这个端点
# 只要求"已登录"，任何账号都能轮询——没有这道闸，32 个并发轮询就能让全站登录排队一秒。
# 取 5 秒：监控图表的粒度本就不需要更细，而它把单账号能造成的线程占用降到 1/5。
MIN_SAMPLE_INTERVAL_SECONDS = 5
# 历史样本上限。监控对象是长驻单例，不设上限就是一条按请求量增长的内存泄漏。
HISTORY_MAX = 120
ALERT_MAX = 200
# 摘要只看最近这么多个样本
SUMMARY_WINDOW = 10


class SystemMonitor:
    """系统性能监控。

    进程内长驻单例：摘要要跨请求才有意义。此前每个请求新建一个实例，
    `metrics_history` 永远只有一个样本，"最近 10 次的平均"实际上在平均它自己
    刚采的那一个点。

    注意单例是**每 worker 一份**（`start.sh` 固定 4 个 uvicorn worker），所以摘要
    描述的是接下这次请求的那个 worker 看到的样本序列，不是全机的。指标本身取自
    宿主机（psutil），跨 worker 一致；受影响的只有"最近 N 次"这个窗口的采样密度。
    """

    def __init__(self):
        self.metrics_history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self.alerts: deque[dict] = deque(maxlen=ALERT_MAX)
        # 采样闸：真实采样是阻塞的，窗口内的并发请求共用同一份结果而不是各采一次。
        self._lock = threading.Lock()
        self._last_sampled_at = 0.0

    def collect_metrics(self) -> dict:
        """取一份系统指标；距上次真实采样不足 `MIN_SAMPLE_INTERVAL_SECONDS` 则复用。

        **真实采样会阻塞一个采样窗口**，调用方必须放进线程池。

        锁是为了让并发请求里只有一个真的去采样：没有它，同时到达的 N 个请求会各占一条
        线程各阻塞一秒，而这正是要挡掉的那件事。持锁期间确实会阻塞其余请求，但它们本来
        就要等这一秒，等完拿到的还是同一份数据。
        """
        with self._lock:
            now = time.monotonic()
            if self.metrics_history and now - self._last_sampled_at < MIN_SAMPLE_INTERVAL_SECONDS:
                return self.metrics_history[-1]
            metrics = self._sample()
            self._last_sampled_at = time.monotonic()
            return metrics

    def _sample(self) -> dict:
        """真正采一次。阻塞 `SAMPLE_INTERVAL_SECONDS`。"""
        # 只采一次 percpu，总占用率取各核均值——这正是 psutil.cpu_percent() 的算法。
        # 分两次采样是没有必要的：两次各阻塞一个窗口，代价翻倍而信息量不变。
        cpu_per_core = psutil.cpu_percent(interval=SAMPLE_INTERVAL_SECONDS, percpu=True)
        cpu_percent = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        metrics = {
            'timestamp': datetime.now().isoformat(),
            'cpu': {
                'total': round(cpu_percent, 1),
                'cores': cpu_per_core,
                'load_avg': list(psutil.getloadavg()[:3]) if hasattr(psutil, 'getloadavg') else [0, 0, 0]
            },
            'memory': {
                'percent': memory.percent,
                'available_gb': memory.available / (1024 ** 3),
                'used_gb': memory.used / (1024 ** 3)
            },
            'disk': {
                'percent': disk.percent,
                'free_gb': disk.free / (1024 ** 3)
            },
            'processes': len(psutil.pids())
        }

        self.metrics_history.append(metrics)
        self.check_alerts(metrics)

        return metrics

    def check_alerts(self, metrics: dict):
        """检查告警条件"""
        alerts = []

        # CPU告警
        if metrics['cpu']['total'] > 90:
            alerts.append({
                'level': 'warning',
                'message': f"CPU使用率过高: {metrics['cpu']['total']:.1f}%"
            })

        # 内存告警
        if metrics['memory']['percent'] > 85:
            alerts.append({
                'level': 'warning',
                'message': f"内存使用率过高: {metrics['memory']['percent']:.1f}%"
            })

        # 磁盘空间告警
        if metrics['disk']['percent'] > 80:
            alerts.append({
                'level': 'critical',
                'message': f"磁盘空间不足: {metrics['disk']['percent']:.1f}%"
            })

        # 记录告警。走 logger 而非 print——print 不带时间戳、不进 logs/app.log，
        # 等于告警只在前台运行时可见。
        for alert in alerts:
            alert['timestamp'] = datetime.now().isoformat()
            self.alerts.append(alert)
            logger.warning("🚨 [%s] %s", alert['level'].upper(), alert['message'])

    def get_performance_summary(self) -> dict:
        """获取性能摘要（最近若干次样本的均值）。"""
        if not self.metrics_history:
            return {}

        recent = list(self.metrics_history)[-SUMMARY_WINDOW:]

        avg_cpu = sum(m['cpu']['total'] for m in recent) / len(recent)
        avg_memory = sum(m['memory']['percent'] for m in recent) / len(recent)
        # 核数按实际样本取，不写死。写死 4 核会在核数更少的机器上直接 IndexError,
        # 而这是个"看机器状态"的端点，它自己先崩掉最没道理。
        core_count = min(len(m['cpu']['cores']) for m in recent)
        # 一小时内的告警数。此前用 `now().replace(hour=now().hour - 1)` 算窗口下界，
        # 0 点到 1 点之间 hour 会变成 -1，replace 直接抛 ValueError——每天固定崩一小时。
        since = datetime.now() - timedelta(hours=1)

        return {
            'samples': len(self.metrics_history),
            'avg_cpu_usage': round(avg_cpu, 1),
            'avg_memory_usage': round(avg_memory, 1),
            'cpu_core_utilization': [
                round(sum(m['cpu']['cores'][i] for m in recent) / len(recent), 1)
                for i in range(core_count)
            ],
            'recent_alerts_count': len(
                [a for a in self.alerts if datetime.fromisoformat(a['timestamp']) > since]
            ),
        }
