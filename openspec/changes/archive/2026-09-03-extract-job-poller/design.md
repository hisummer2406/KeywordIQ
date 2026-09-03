## Context

`app/table/upload/import_queue.py` 里的 `run_queue_drainer` 当前顺序执行四件事：

```
while True:
    reap_stale_uploads()              # 上传领域：回收超时直传批次，恒执行
    purge_expired_competitor_previews()  # 竞对领域：清理过期预览行，恒执行
    batch = drain_import_queue(...)   # 上传领域：抽一个排队批次
    if batch is not None:
        continue                      # 抽到了，整轮从头重来
    if refresh_due_dtc_store():       # 竞对领域：拉一个到期独立站
        continue                      # 拉到了，整轮从头重来
    # 都没事可做
    sleep(interval)
```

四个动作分属两个领域（上传、竞对），但调度它们的代码物理上全部挤在上传领域的文件里。
这不是本来就该在一起——`reap_stale_uploads` 和 `refresh_due_dtc_store` 除了"被同一个
`while True` 调用"之外没有任何耦合，各自的异常处理、日志文案、是否走线程池都完全独立。

本设计没有数据库改动，不涉及 Alembic；也没有改变任何查询/导入路径的连接池占用、内存
占用或执行计划——纯粹是把已有的四次函数调用按领域重新归位，外加一层通用的调度骨架。

## Goals / Non-Goals

**Goals:**
- 把"轮询节奏、任务优先级、取消、顶层异常隔离"这四件与具体领域无关的编排职责，
  抽成一个不依赖任何领域模块的 `app/jobs/poller.py`
- 让每个领域自己的"一轮工作"入口回到各自的领域文件里，`import_queue.py` 只剩导入
  领域自己的两件事
- 给这层此前完全没有测试覆盖的调度逻辑（顺序、continue 语义、异常隔离、取消）补上
  单元测试
- 现有调度顺序、异常隔离策略、取消行为、导入优先级逐一对应保留，不做任何行为改变

**Non-Goals:**
- 不支持多条独立的轮询循环（多个 interval、多组互不相关的 job 列表）——当前只有
  一条循环在用它，提前做成"通用多循环框架"是没有第二个调用方验证的抽象，YAGNI
- 不改变四个动作各自的实现（`drain_import_queue`、`reap_stale_uploads`、
  `purge_expired_previews`、`refresh_due_store` 内部逻辑一行不动）
- 不改变 `IMPORT_QUEUE_POLL_SECONDS` 的语义或默认值
- 不做 issue #69 的阶段二（Analysis 页面拆分）或阶段三（`store_service.py` 内部拆分）

## Decisions

### 1. Job 的类型是 `async () -> bool`，`True` 语义是"整轮重来"

`run_poller` 只认识一件事：一串没有参数、返回布尔值的异步可调用对象，按传入顺序
（即优先级）逐个执行；哪个返回 `True` 就立即从头重跑整轮 job 列表（对应原代码里的
`continue`），全部返回 `False` 才 `sleep(interval)`。

这个约定天然复现了原代码的两类动作：

- **"恒执行、不参与重跑判定"的维护性任务**（回收超时上传、清理过期预览）：包装函数
  内部无论做没做事，一律 `return False`——它们因此在每一轮都会被执行到（哪怕前一个
  job 触发了整轮重来），与原代码里 `reap_stale_uploads()` / `purge_...()` 恒排在
  `while True` 顶部、每次循环都跑一遍完全一致。
- **"做了就立即重来"的工作任务**（抽一个批次、拉一个到期站点）：包装函数按实际是否
  做了事返回布尔值，`True` 触发整轮重来，让积压的批次/站点连续处理、不必多等一个
  `interval`。

选这个约定而不是"返回一个枚举"或"job 自己决定要不要 continue"，是因为现有代码的
`continue` 语义本来就只有这一个二值判断——加一个三态或回调式控制流是没有对应需求的
过度设计。

**考虑过的替代方案**：让每个 job 自己包一层，返回 `None` 表示"不参与判定"、
`bool` 表示"参与判定"。放弃是因为这会让 `run_poller` 内部多一次类型判断，而"维护性
任务恒 `False`"已经能表达同样的语义，不需要三态。

### 2. 异常隔离分两层，领域内的具体处理不挪进编排层

`run_poller` 只提供**顶层**兜底：整轮 job 执行被一个 `try/except Exception` 包住，
异常发生时记日志、跳过本轮剩余 job、落到 `sleep` 分支；`CancelledError` 直接
`raise` 放行，不吞。这是安全网，不是主要防线。

**每个领域自己的包装函数保留原有的 `try/except`**（比如 `reap_stale_uploads_round`
内部仍然 `try: reap_stale_uploads(...) except Exception: logger.exception("回收
超时直传批次时出错，本轮跳过")`）。原因是现有代码里每一处 `try/except` 都对应一条
写在注释里的具体理由（"回收失败不该连累排队接手""预览清理是附带工作，失败不该连累
排队接手"），这些理由是每个领域自己的判断，不是通用编排逻辑——把它们收进 `run_poller`
会让 `run_poller` 变成"知道每个 job 该怎么出错"的上帝函数，且丢失现有的领域专属日志
文案（`logger.exception` 的具体措辞是排查时唯一的线索来源，见 `CLAUDE.md` 错误契约
一节）。

`run_poller` 的顶层 `try/except` 因此只会在**领域包装函数自己没接住**的异常上触发
——例如 `drain_one_batch_round` 里 `drain_import_queue` 本身抛出的异常（原代码里
这条调用同样没有自己的 `try/except`，是靠 `run_queue_drainer` 外层的 `try` 兜底），
这与原行为完全对应。

### 3. 各领域的包装函数落回各自的领域文件，不集中放一处

`reap_stale_uploads_round` / `drain_one_batch_round` 留在 `import_queue.py`
（上传领域自己的两件事）；`purge_expired_previews_round` 挪进 `watch_service.py`、
`refresh_due_store_round` 挪进 `store_service.py`（各自紧邻它们包装的领域函数）。

这是本变更与"只是把四个函数塞进一个新文件"的关键区别：如果四个包装函数集中放进
`app/jobs/`，`app/jobs/` 就会变成一个新的、需要同时理解四个领域生命周期的地方——
与本变更要解决的问题是同一件事,只是换了个文件名。让包装函数留在各自领域,
`app/jobs/poller.py` 因此不 import 任何 `app/table/*` 模块，是真正意义上的中立层。

### 4. Job 列表的组装位置是 `main.py`

`main.py` 的 lifespan 本来就是跨领域组装的地方（同一段代码里已经在做数据库连接
探活、SSO 默认角色校验等跨领域启动检查）。把"这一轮轮询到底调度哪些 job、按什么
顺序"的组装放在这里，而不是新建一个 `app/jobs/registry.py`，是因为组装本身就是
一次性的、只有一个调用点的逻辑——多一个注册表模块只是多一层间接，没有第二个组装点
可以验证这层抽象的必要性。

## Risks / Trade-offs

- **[风险] 泛化后的循环控制流比原来的显式 `while/continue` 更抽象一层，读者需要先理解
  "job 列表 + True 触发重跑"这个约定才能读懂调度顺序。**
  → 缓解：`app/jobs/poller.py` 的模块 docstring 和 `run_poller` 的函数 docstring
  直接写明这个约定（含"哪类 job 该恒返回 False"的判据），并在 `main.py` 组装 job
  列表处用注释复述现有的优先级顺序原因（"先回收上传→再清预览→再排空导入→队列空了
  才拉独立站，导入是用户在等的事、拉取不是"），不指望读者只看 `poller.py` 就能推断出
  顺序背后的业务原因。
- **[风险] 补充的单元测试是这次改动唯一的新增覆盖，如果测试本身没有覆盖"顶层异常不
  影响下一轮"这个此前从未验证过的行为，拆分可能悄悄改变这一点而不被发现。**
  → 缓解：tasks.md 里显式列出这条断言（异常发生的这一轮里，后续 job 不再执行；
  下一轮从 job 列表第一个重新开始，不记住上一轮在哪个 job 出错）。
- **[风险] `refresh_due_store_round` 与 `purge_expired_previews_round` 迁移后，
  `import_queue.py` 不再 import `store_service` 与 `watch_service`——如果遗漏更新
  `main.py` 里原来经 `import_queue` 间接可见的 import，会在启动时才暴露成
  `ImportError`，而不是在 review 阶段就看出来。**
  → 缓解：`main.py` 改为直接从 `store_service` / `watch_service` / `import_queue`
  三个模块各自导入对应的 `*_round` 函数，任务列表在 lifespan 里用三行 import +
  一个字面量列表拼出来，import 路径与来源模块一一对应、不经过任何中间模块转手。

## Migration Plan

无数据库改动、无配置项改动，是纯代码内部重排，单个 PR 一次性完成：

1. 新建 `app/jobs/poller.py` + 单元测试，先跑通、不接入任何调用方
2. 三个领域文件（`import_queue.py` / `watch_service.py` / `store_service.py`）
   各自新增/改名对应的 `*_round` 函数，函数体逻辑不变，暂时新旧函数并存
3. `main.py` 切换调用方为 `run_poller` + 新 job 列表
4. 删除 `import_queue.py` 里已经不再被任何地方引用的旧函数
   （`run_queue_drainer`、`_purge_expired_competitor_previews`、`_refresh_due_dtc_store`）
5. 本地起服务验证轮询任务正常启动、`IMPORT_QUEUE_POLL_SECONDS<=0` 时仍然按既有逻辑
   跳过任务创建；`uv run pytest test/ -q` 全绿

**回滚**：单个 PR、合并提交（非 squash），出问题直接 `git revert` 该合并提交；
没有数据库迁移需要单独回滚。

## Open Questions

无——所有关键设计决策已在上面的 Decisions 一节落定，proposal.md 的约束（不改
API/契约/权限点/调度顺序）已覆盖验收标准里能预先判断的部分。
