## ADDED Requirements

### Requirement: Job 按传入顺序执行，不重排、不并发
`run_poller` 接收一串 `async () -> bool` 的可调用对象（job）。每一轮 SHALL 严格按
传入的顺序逐个 `await` 执行，MUST NOT 并发执行，MUST NOT 按其他标准（如上一轮是否
做过事）重排顺序。

#### Scenario: 多个 job 依次执行
- **WHEN** 传入 job 列表 `[a, b, c]`，且 a、b、c 都返回 `False`
- **THEN** 三者按 a → b → c 的顺序被依次 `await`，且都在同一轮里执行完

### Requirement: 任一 job 返回 True 时整轮立即从头重跑
一个 job 返回 `True` 表示"这一轮真的做了有意义的工作"。此时 `run_poller` SHALL
立即停止执行本轮列表中排在它之后的其余 job，并 SHALL 从列表第一个 job 重新开始
下一轮，MUST NOT 等待 `sleep` 间隔。

#### Scenario: 中间 job 返回 True，跳过后续 job
- **WHEN** 传入 job 列表 `[a, b, c]`，a 返回 `False`，b 返回 `True`
- **THEN** c 在这一轮不被执行；下一轮从 a 重新开始（而不是从 c 或从 b 之后继续）

#### Scenario: 连续多轮都有工作可做时不睡眠
- **WHEN** 某个 job 连续多轮都返回 `True`（比如队列里积压了多个待处理项）
- **THEN** 每一轮之间不触发 `sleep`，直到某一轮全部 job 都返回 `False` 为止

### Requirement: 全部 job 返回 False 时才按间隔睡眠
只有当一整轮里所有 job 都返回 `False`（没有任何一个报告"做了工作"）时，`run_poller`
SHALL 在轮次结束后 `sleep(interval_seconds)`，再开始下一轮。

#### Scenario: 无事可做时按间隔睡眠
- **WHEN** 传入 job 列表 `[a, b]`，a 与 b 本轮都返回 `False`
- **THEN** 本轮结束后触发一次 `sleep(interval_seconds)`，而不是立即开始下一轮

### Requirement: 顶层异常隔离，不中断轮询
job 执行过程中抛出的、未被 job 自身捕获的异常，`run_poller` SHALL 在顶层捕获并记录
日志，MUST NOT 重新抛出该异常；捕获后 SHALL 视为本轮结束（不再执行本轮剩余 job），
并进入 `sleep(interval_seconds)` 后开始下一轮。异常 MUST NOT 导致 `run_poller` 退出。

#### Scenario: 某个 job 抛出未捕获异常
- **WHEN** 传入 job 列表 `[a, b]`，a 抛出异常
- **THEN** b 在这一轮不被执行；异常被记录日志；`run_poller` 不退出、不重新抛出该异常，
  经过一次 `sleep(interval_seconds)` 后从 a 开始下一轮

#### Scenario: 某一轮异常不影响后续轮次正常执行
- **WHEN** 第 N 轮某个 job 抛出异常，第 N+1 轮同一个 job 正常返回
- **THEN** 第 N+1 轮及之后的轮询按正常顺序继续执行，不因上一轮的异常残留任何状态

### Requirement: CancelledError 必须透传，不被顶层异常隔离吞掉
`asyncio.CancelledError`（任务被取消，如应用关闭时对轮询任务调用 `.cancel()`）
MUST NOT 被顶层异常隔离捕获吞掉，SHALL 原样向上 `raise`，使 `run_poller` 所在的
`asyncio.Task` 能被正常取消退出。

#### Scenario: 轮询任务被取消
- **WHEN** 某个 job 执行期间或 `sleep` 期间，包裹 `run_poller` 的 `asyncio.Task`
  被 `.cancel()`
- **THEN** `asyncio.CancelledError` 从 `run_poller` 中向上抛出，不被记录为普通异常、
  不触发 `sleep` 后继续下一轮

### Requirement: run_poller 不感知任何具体领域
`app/jobs/poller.py` 模块 MUST NOT import 任何 `app/table/*` 下的领域模块；job
内部如何执行（是否需要 `run_in_threadpool`、内部异常如何处理与记日志）SHALL 完全
由传入的 job 可调用对象自行决定，`run_poller` MUST NOT 对 job 的内部实现做任何假设。

#### Scenario: poller 模块的依赖范围
- **WHEN** 检查 `app/jobs/poller.py` 的 import 列表
- **THEN** 其中不包含任何 `app.table.*` 模块
