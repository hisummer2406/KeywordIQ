## 1. `app/jobs/poller.py`：中立轮询编排

- [ ] 1.1 新建 `app/jobs/poller.py`：定义 `PollerJob = Callable[[], Awaitable[bool]]`
      类型别名，实现 `async def run_poller(jobs: Sequence[PollerJob], interval_seconds: float, *, name: str) -> None`；
      模块 docstring 写明"job 返回 True 触发整轮重跑、全部 False 才 sleep"这一约定，
      以及"哪类 job 该恒返回 False"的判据（维护性任务 vs 工作任务）
- [ ] 1.2 `run_poller` 循环体：按顺序 `await` 每个 job，任一返回 `True` 立即 `break`
      本轮并跳过 `sleep`、从下一轮循环顶部重新开始；`try/except asyncio.CancelledError: raise`
      放在最外层、`except Exception: logger.exception(...)` 兜底
- [ ] 1.3 启动日志沿用原有文案风格（如 `"🧹 %s 轮询任务已启动（每 %d 秒一轮）"`），
      用 `name` 参数替换原来写死的"导入队列接手任务"
- [ ] 1.4 单元测试 `test/test_job_poller_unit.py`（fake job，不连数据库）：
  - [ ] 1.4.1 多个 job 按传入顺序依次执行（用一个共享 list 记录调用顺序断言）
  - [ ] 1.4.2 中间 job 返回 `True` 时，本轮排在它之后的 job 不被执行；下一轮从
        列表第一个重新开始
  - [ ] 1.4.3 连续多轮都有 job 返回 `True` 时，轮次之间不触发 `sleep`（mock
        `asyncio.sleep`，断言未被调用）
  - [ ] 1.4.4 全部 job 返回 `False` 时，本轮结束后触发一次 `sleep(interval_seconds)`
  - [ ] 1.4.5 某个 job 抛出未捕获异常：本轮排在它之后的 job 不被执行、异常被
        `logger.exception` 记录、`run_poller` 不重新抛出，经过一次 `sleep` 后
        下一轮从头开始且能正常继续
  - [ ] 1.4.6 `asyncio.CancelledError` 从 job 或 `sleep` 中抛出时，原样向上传播，
        不被当作普通异常捕获
  - [ ] 1.4.7 静态断言：`app/jobs/poller.py` 源码不 import 任何 `app.table.*` 模块
        （用 `ast` 解析该文件的 import 语句逐条检查，而不是字符串匹配文件文本）

## 2. `import_queue.py`：收缩回导入领域

- [ ] 2.1 `_reap_stale_uploads` 改名为 `async def reap_stale_uploads_round() -> bool`
      并转为 `async`（内部同步调用仍走 `run_in_threadpool`）；保留既有 `OSS_ENABLED`
      短路与 `try/except Exception: logger.exception(...)`；恒返回 `False`
- [ ] 2.2 新增 `async def drain_one_batch_round() -> bool`：包一次
      `await run_in_threadpool(drain_import_queue, source_factory=oss_source_factory)`，
      批次不为空时保留原有 `logger.info("排队批次已接手完成 ...")` 并返回 `True`，
      否则返回 `False`
- [ ] 2.3 删除 `run_queue_drainer`、`_purge_expired_competitor_previews`、
      `_refresh_due_dtc_store`（迁移到各自领域后，原地不再需要）
- [ ] 2.4 `oss_source_factory` / `oss_object_size` 保持不变
- [ ] 2.5 模块 docstring 更新：说明本文件现在只负责导入领域自己的排队接手与上传回收，
      轮询编排与其余领域的调度已经不在这里，指向 `app/jobs/poller.py` 与
      `main.py` 的 job 列表组装处

## 3. 竞对领域：迁移各自的"一轮工作"入口

- [ ] 3.1 `app/table/competitor/watch_service.py` 新增
      `async def purge_expired_previews_round() -> bool`，紧邻 `purge_expired_previews`
      定义；内部走 `run_in_threadpool` + 原有 `try/except Exception: logger.exception(...)`
      文案不变；恒返回 `False`
- [ ] 3.2 `app/table/competitor/store_service.py` 新增
      `async def refresh_due_store_round() -> bool`（原 `import_queue.py` 里
      `_refresh_due_dtc_store` 的逻辑原样迁移改名），紧邻 `refresh_due_store` 定义；
      保留"网络段直接 `await`、只有内部数据库写入走 `run_in_threadpool`"的既有实现与
      注释、原有 `try/except Exception: logger.exception(...)` 文案不变；按
      `refresh_due_store() is not None` 返回布尔值

## 4. `main.py`：组装 job 列表

- [ ] 4.1 lifespan 里的 import 从
      `from app.table.upload.import_queue import run_queue_drainer`
      改为分别从 `import_queue` / `watch_service` / `store_service` 导入对应的
      三个 `*_round` 函数，另加 `from app.jobs.poller import run_poller`
- [ ] 4.2 组装 job 列表 `jobs = [reap_stale_uploads_round, purge_expired_previews_round, drain_one_batch_round, refresh_due_store_round]`
      并加注释复述现有优先级原因（先回收上传 → 再清预览 → 再排空导入 → 队列空了才拉
      独立站，导入是用户在等的事、拉取不是）
- [ ] 4.3 `IMPORT_QUEUE_POLL_SECONDS > 0` 时创建
      `asyncio.create_task(run_poller(jobs, settings.IMPORT_QUEUE_POLL_SECONDS, name="导入队列"))`，
      `<= 0` 时保留原有的 `logger.warning("⚠️ IMPORT_QUEUE_POLL_SECONDS=0，排队批次不会自动接手")`
- [ ] 4.4 关闭事件里 `drainer.cancel()` + `await drainer` 的既有收尾逻辑不变（变量名、
      异常吞掉方式原样保留）

## 5. 收尾验证

- [ ] 5.1 `uv run ruff check .` 通过
- [ ] 5.2 `uv run pytest test/ -q` 全绿（含新增的 `test_job_poller_unit.py`）
- [ ] 5.3 本地起服务（`uv run uvicorn main:app --reload`），确认启动日志出现
      `🧹 导入队列 轮询任务已启动` 字样、无 `ImportError`；临时把
      `IMPORT_QUEUE_POLL_SECONDS` 设为 `0` 重启一次，确认仍然打印
      "不会自动接手" 的既有警告日志（验证完改回原值）
- [ ] 5.4 `openspec validate extract-job-poller` 通过
- [ ] 5.5 提交前确认索引列（`git status --short`）里没有夹带与本变更无关的文件
