"""排障日志自检：把「轮转安全吗」「request_id 真的能关联吗」变成可执行的判据。

两类问题都无法只靠代码走查确认（design.md 的「风险/待验收」一节）：

- 4 个 worker 进程共享同一个日志文件时，轮转是否真的不丢失、不损坏——这只能靠真的
  并发写入 + 真的触发一次文件替换来验证，静态审查看不出运行时的竞争条件。
- nginx/应用之间的 request_id 关联是否真的生效——这依赖 nginx 配置是否被正确装载、
  X-Request-Id 是否真的下发，本地代码看不出生产 nginx 实际装的是哪份配置。

用法::

    # 全部检查
    uv run python scripts/check_logging.py

    # 只查轮转安全性，不需要应用在跑
    uv run python scripts/check_logging.py --skip-http

    # 指定要打请求的地址（默认直连 uvicorn，绕开 nginx；生产要连 nginx 就传公网地址）
    uv run python scripts/check_logging.py --base-url http://127.0.0.1:8000

退出码非 0 表示有检查未通过，可用于部署前门禁。
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_WORKER_COUNT = 4
_LINES_PER_WORKER = 200


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str, hint: str = "") -> None:
        print(f"  ❌ {name} — {detail}")
        if hint:
            print(f"     ↳ {hint}")
        self.failures.append(name)

    def warn(self, name: str, detail: str, hint: str = "") -> None:
        print(f"  ⚠️  {name} — {detail}")
        if hint:
            print(f"     ↳ {hint}")
        self.warnings.append(name)

    def summary(self) -> int:
        print()
        if self.failures:
            print(f"结论：{len(self.failures)} 项未通过 —— {', '.join(self.failures)}")
        else:
            print("结论：排障日志检查全部通过")
        if self.warnings:
            print(f"另有 {len(self.warnings)} 项告警：{', '.join(self.warnings)}")
        return 1 if self.failures else 0


def _worker_write(log_path: str, worker_id: int, barrier) -> None:
    """独立进程：用 WatchedFileHandler 写固定行数，行内容带 worker id + 序号，
    便于事后核对"每个 worker 的每一行都完整出现且只出现一次"。"""
    handler = logging.handlers.WatchedFileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"check_logging.worker.{worker_id}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    barrier.wait()  # 所有 worker 同时开始写，制造真实的并发窗口
    for i in range(_LINES_PER_WORKER):
        logger.info("worker=%d seq=%d", worker_id, i)
        if i == _LINES_PER_WORKER // 2:
            # 留出时间窗口，让主进程有机会在"写到一半"时执行轮转
            time.sleep(0.001)
    handler.close()


def _check_multiprocess_rotation(report: Report) -> None:
    """[1/3] 4 个进程并发写入 + 模拟一次轮转，核对不丢失、不损坏。"""
    print("[1/3] 多进程并发写入 + 轮转安全性")

    with tempfile.TemporaryDirectory(prefix="keywordiq-check-logging-") as tmp:
        log_path = os.path.join(tmp, "app.log")
        barrier = multiprocessing.Barrier(_WORKER_COUNT + 1)  # +1 是主进程自己
        procs = [
            multiprocessing.Process(target=_worker_write, args=(log_path, i, barrier))
            for i in range(_WORKER_COUNT)
        ]
        for p in procs:
            p.start()

        barrier.wait()
        # 让 worker 们先写一部分，制造出"文件已存在且正被写入"的状态，再模拟
        # logrotate 的 create 模式：rename 走 + 新建同名空文件。
        time.sleep(0.05)
        rotated_path = log_path + ".1"
        if os.path.exists(log_path):
            os.rename(log_path, rotated_path)
        Path(log_path).touch()

        for p in procs:
            p.join(timeout=30)

        missing_procs = [p for p in procs if p.exitcode != 0]
        if missing_procs:
            report.fail(
                "worker 进程退出异常",
                f"{len(missing_procs)}/{_WORKER_COUNT} 个进程非正常退出",
            )
            return

        combined = ""
        for p in (rotated_path, log_path):
            if os.path.exists(p):
                combined += Path(p).read_text(encoding="utf-8")

        if "\x00" in combined:
            report.fail(
                "文件内容",
                "发现 NUL 字节空洞——这正是 copytruncate 类方案对多进程写偏移量不安全的表现",
            )
            return

        expected = {
            f"worker={w} seq={i}" for w in range(_WORKER_COUNT) for i in range(_LINES_PER_WORKER)
        }
        actual_lines = [line for line in combined.splitlines() if line]
        actual = set(actual_lines)

        missing = expected - actual
        duplicated = len(actual_lines) - len(actual)

        if missing:
            report.fail(
                "日志完整性",
                f"轮转前后共丢失 {len(missing)} 条记录（应有 {len(expected)} 条）",
                "WatchedFileHandler 应该在下一次 emit() 时检测到 (dev, ino) 变化并重新打开",
            )
        elif duplicated:
            report.fail("日志完整性", f"发现 {duplicated} 条重复记录")
        else:
            report.ok(
                "日志完整性",
                f"{_WORKER_COUNT} 个进程各写 {_LINES_PER_WORKER} 条，轮转前后共 {len(expected)} 条全部完整、无重复、无空洞",
            )


def _check_logrotate_config(report: Report) -> None:
    """[2/3] logrotate 配置文件语法（如果本机装了 logrotate）。"""
    print("[2/3] logrotate 配置语法")
    conf_path = Path(__file__).resolve().parent.parent / "etc" / "keywordiq-logrotate.conf"
    if not conf_path.exists():
        report.fail("配置文件", f"{conf_path} 不存在")
        return

    logrotate_bin = shutil.which("logrotate")
    if not logrotate_bin:
        report.warn(
            "logrotate 未安装",
            "本机没有 logrotate 命令，跳过语法校验",
            "生产部署时用 `logrotate -d /etc/logrotate.d/keywordiq` 空跑校验",
        )
        return

    result = subprocess.run(
        [logrotate_bin, "-d", str(conf_path)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        report.fail("配置语法", result.stderr.strip() or "logrotate -d 返回非零退出码")
    else:
        report.ok("配置语法", "logrotate -d 空跑通过")
        report.warn(
            "路径占位符",
            "配置文件里的 /path/to/keywordiq/logs/app.log 需要按实际部署路径替换",
            "部署时编辑 etc/keywordiq-logrotate.conf 再装入 /etc/logrotate.d/",
        )


def _check_request_id_correlation(report: Report, base_url: str) -> None:
    """[3/3] 打一个带自定义 X-Request-Id 的请求，核对响应头回显 + 本地日志能查到。"""
    print("[3/3] request_id 关联")
    import requests

    probe_id = uuid.uuid4().hex[:8]
    try:
        resp = requests.get(f"{base_url}/", headers={"X-Request-Id": probe_id}, timeout=5)
    except requests.RequestException as exc:
        report.warn(
            "应用未响应",
            f"无法连接 {base_url}（{exc}），跳过此项",
            "先启动应用（uv run uvicorn main:app）再跑本项检查，或用 --skip-http 跳过",
        )
        return

    echoed = resp.headers.get("X-Request-Id")
    if echoed != probe_id:
        report.fail(
            "响应头回显",
            f"期望 X-Request-Id={probe_id}，实际得到 {echoed!r}",
        )
        return
    report.ok("响应头回显", f"X-Request-Id={probe_id} 原样回显")

    log_path = Path("logs/app.log")
    if not log_path.exists():
        report.warn(
            "本地日志文件不可读",
            f"{log_path} 不存在（可能是从远程地址探测，本机拿不到日志文件）",
            "在应用所在主机上运行本脚本才能核对这一项",
        )
        return

    # 给异步的日志写入一点时间落盘
    time.sleep(0.2)
    content = log_path.read_text(encoding="utf-8", errors="ignore")
    if probe_id not in content:
        report.fail(
            "日志可检索性",
            f"logs/app.log 里找不到 {probe_id}",
            "确认请求摘要中间件已挂载、且 / 不在 _SKIP_PATHS 里",
        )
    else:
        report.ok("日志可检索性", f"{probe_id} 在 logs/app.log 中可被检索到")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="应用地址，默认直连本机 uvicorn")
    parser.add_argument("--skip-http", action="store_true", help="跳过第 3 项（不需要应用在跑）")
    args = parser.parse_args(argv)

    report = Report()
    print("排障日志自检\n")

    _check_multiprocess_rotation(report)
    _check_logrotate_config(report)
    if not args.skip_http:
        _check_request_id_correlation(report, args.base_url)
    else:
        print("[3/3] request_id 关联（--skip-http，已跳过）")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
