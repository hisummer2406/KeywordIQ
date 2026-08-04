# monitoring.py
"""宿主机指标采样。

**全部是同步阻塞调用**（psutil 的采样要真的等一个采样窗口），所以这里刻意不写成
`async def`——写成 async 只会让调用方误以为它可以在事件循环上直接 await，而那正是
让整个 worker 停摆一整个采样窗口的写法。调用方负责把它放进线程池。
"""
import logging
from collections import deque
from datetime import datetime, timedelta

import psutil

logger = logging.getLogger(__name__)

# 采样窗口。psutil 要在这段时间里对比两次 CPU 时间片才能算出占用率。
SAMPLE_INTERVAL_SECONDS = 1
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
    """

    def __init__(self):
        self.metrics_history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self.alerts: deque[dict] = deque(maxlen=ALERT_MAX)

    def collect_metrics(self) -> dict:
        """采集一次系统指标。**阻塞一个采样窗口**，调用方必须放进线程池。"""
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
