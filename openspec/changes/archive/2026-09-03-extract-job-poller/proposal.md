## Why

`app/table/upload/import_queue.py` 名义上属于上传领域，但它的轮询循环
`run_queue_drainer` 实际调度了四件互不相干的事：排队导入接手、超时上传回收（上传领域）、
竞对预览清理（竞对领域）、独立站到期拉取（竞对领域）。继续在这个文件里叠加后台任务，
会让"改一个领域的调度行为"必须先理解另外三个领域的生命周期，回归范围随之扩大——这正是
issue #69 记录的三个局部热点之一。

本变更是 issue #69 建议实施顺序的第一阶段：把轮询节奏、优先级、取消与顶层异常隔离这些
与具体领域无关的编排逻辑，抽成一个中立的后台任务编排模块；各领域只负责暴露自己的
"一轮工作"入口，`import_queue.py` 收缩回只保留导入领域自己的两件事。

日报路径、周报路径均不受影响：本变更不改变导入的数据处理逻辑，只重排"谁在什么时候
调用它"这一层。

## What Changes

- 新增 `app/jobs/poller.py`：提供通用的轮询编排 `run_poller(jobs, interval_seconds, name)`。
  `jobs` 是一串 `async () -> bool` 的可调用对象，按传入顺序（即优先级）逐个执行；任一个
  返回 `True`（真的做了有意义的工作）就立即从头重跑整轮，全部返回 `False` 才 `sleep`；
  顶层 `try/except Exception` 兜底记日志、`CancelledError` 直接 `raise` 放行。这四点是
  `run_poller` 的全部职责，具体每个 job 内部怎么跑（是否要 `run_in_threadpool`、出错要不要
  连累后续 job）由各领域自己决定，编排层不管。
- `app/table/upload/import_queue.py` 收缩为只保留导入领域自己的能力：`oss_source_factory`、
  `oss_object_size`、`reap_stale_uploads_round`（原 `_reap_stale_uploads` 改名转
  `async`，逻辑与既有 `OSS_ENABLED` 短路、内部 `try/except` 不变）、`drain_one_batch_round`
  （包一次 `drain_import_queue` 调用，批次不为空才返回 `True`，日志不变）。
- `purge_expired_previews_round` 从 `import_queue.py` 迁移到
  `app/table/competitor/watch_service.py`，紧邻它包装的 `purge_expired_previews`；
  竞对到期拉取的包装函数从 `import_queue.py` 的 `_refresh_due_dtc_store` 迁移改名为
  `refresh_due_store_round`，落到 `app/table/competitor/store_service.py`，紧邻
  `refresh_due_store`。两者的既有 `try/except`、日志文案与"网络段直接 `await`、只有
  数据库写入走线程池"的实现细节原样保留，恒返回各自领域已有的语义（预览清理恒 `False`，
  独立站拉取按是否真的拉到返回布尔值）。
- `main.py` 的 lifespan 里按现有优先级顺序（回收上传 → 清理预览 → 排空导入 → 拉独立站）
  组装 job 列表，调用 `run_poller(jobs, settings.IMPORT_QUEUE_POLL_SECONDS, name="导入队列")`
  替换原来对 `run_queue_drainer(interval)` 的直接调用；`IMPORT_QUEUE_POLL_SECONDS <= 0`
  时不创建任务、关闭时 `cancel()` + `await` 收尾的既有逻辑不变。
- 新增 `app/jobs/poller.py` 的单元测试：多 job 按顺序执行、任一 `True` 立即重跑整轮、
  全 `False` 才 `sleep`、内部异常被顶层捕获且不影响下一轮、`CancelledError` 透传。用
  fake job（不连数据库）覆盖轮询循环本身——这一层此前是零单元测试覆盖，只有集成测试
  间接触达 `drain_import_queue`/`reap_stale_uploads` 各自的实现，循环调度逻辑完全没测。

## Capabilities

### New Capabilities

- `background-job-poller`：`app/jobs/poller.py` 提供的通用轮询编排契约——job 列表按
  传入顺序执行、任一 `True` 触发整轮从头重跑、全部 `False` 才 `sleep`、顶层异常隔离、
  `CancelledError` 透传。这不是面向 HTTP 客户端的产品能力，而是一条"错了不会报错"的
  内部调度语义（顺序错了、异常隔离漏了都不会在接口层报错，只会在生产环境悄悄退化成
  "某个任务再也不跑了"）——按 `CLAUDE.md` 的判据，这类决策值得留档并写成可测试的
  requirement，而不是只留在代码注释里。

### Modified Capabilities

无。本变更不改变任何既有 API 路径、请求/响应契约、权限点或页面文案；四个 job 各自的
业务逻辑（导入接手、上传回收、预览清理、独立站拉取）与它们的执行顺序、异常隔离策略、
取消行为与导入优先级，相对改造前逐一对应一致——这是 issue #69 明确的验收标准。

## Impact

**受影响文件**：
- 新增 `app/jobs/poller.py`（及对应单元测试 `test/test_job_poller_unit.py`）
- `app/table/upload/import_queue.py`：删除 `run_queue_drainer`、`_purge_expired_competitor_previews`、
  `_refresh_due_dtc_store`；`_reap_stale_uploads` 改名 `reap_stale_uploads_round` 并转
  `async`；新增 `drain_one_batch_round`
- `app/table/competitor/watch_service.py`：新增 `purge_expired_previews_round`
- `app/table/competitor/store_service.py`：新增 `refresh_due_store_round`
- `main.py`：lifespan 里替换 `run_queue_drainer` 调用为 `run_poller` + job 列表组装

**不受影响**：所有 API 端点、前端、数据库 schema、导入/查询/分区/保留逻辑、
`IMPORT_QUEUE_POLL_SECONDS` 等既有配置项的含义。日报与周报两条数据路径均不涉及。
