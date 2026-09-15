## 1. 日志轮转（决策 A）

- [x] 1.1 `main.py::configure_logging()` 改用 `logging.handlers.WatchedFileHandler`
      替换 `TimedRotatingFileHandler`，日志格式加 `%(process)d`
- [x] 1.2 新增 `etc/keywordiq-logrotate.conf`（daily、rotate 7、compress、
      delaycompress、`create 644`，不用 `copytruncate`）
- [x] 1.3 单元测试：`WatchedFileHandler` 在文件被 rename+重建后，下一次 `emit()`
      能重新打开新文件并写入（`test/test_logging_config_unit.py`，用临时目录模拟
      rename，不依赖真实 logrotate）
- [x] 1.4 `etc/observability-runbook.md` 起草，先写第 1/2 节骨架（关联本组产出，
      内容随第 5 节一并补全）

## 2. 请求摘要中间件与 nginx 关联（决策 B、C）

- [x] 2.1 新增 `app/common/request_logging.py::RequestLoggingMiddleware`（纯
      ASGI，包装 `send` 观察 `http.response.start`/`http.response.body`）
- [x] 2.2 `main.py` 挂载该中间件（在 `RequestIdMiddleware` 之前 `add_middleware`，
      因此比它更内层，实际实现顺序：CORS → RequestLogging → RequestId），
      跳过 `/health` 路径
- [x] 2.3 单元测试覆盖三种结束形态：`completed`、`interrupted`（模拟客户端提前
      断连、无异常返回）、`error`/`error_before_response`（响应头前后两种异常路径）
      （`test/test_request_logging_unit.py`）
- [x] 2.4 单元测试确认摘要记录不含 query string
- [x] 2.5 单元测试确认 `/health` 不产生摘要记录
- [x] 2.6 `etc/keywordiq-nginx.conf` 新增自定义 `log_format`
      （`$request_time`/`$upstream_response_time`/`$upstream_status`/
      `$request_id`）与 `proxy_set_header X-Request-Id $request_id;`
- [x] 2.7 新增 `test/test_request_context_unit.py` 固化入站头长度/可打印字符
      校验、缺失时生成新 id、以及并发场景下 contextvar 不串号

## 3. 慢查询：通用 SQL 级（决策 D 第一层）

- [x] 3.1 `config.py` 新增 `SLOW_QUERY_THRESHOLD_SECONDS: float = 1.0`（含
      `field_validator` 确保为正数），同步更新 `.env.example`
- [x] 3.2 `database.py`：`before_cursor_execute`/`after_cursor_execute` 抽成
      `_register_slow_query_listener(target_engine)`，同时注册到 `engine` 与
      `async_engine.sync_engine`（异步引擎的 DBAPI 级事件挂在其内部同步 Engine
      上才会触发），阈值改读 `settings.SLOW_QUERY_THRESHOLD_SECONDS`
- [x] 3.3 单元测试（`test/test_slow_query_unit.py`，sqlite 内存引擎，不依赖真实
      PostgreSQL）+ 集成测试（`test/test_slow_query_integration.py`，`TEST_DATABASE_URL`
      门禁，真实 asyncpg 连接验证异步引擎确实会触发）——本地已用
      `keywordiq_test` 跑过，2 项全过

## 4. 分析查询屏慢请求诊断（决策 D 第二层）

- [x] 4.1 `config.py` 新增 `ANALYSIS_SLOW_QUERY_THRESHOLD_MS: int = 1000`，
      同步更新 `.env.example`
- [x] 4.2 `app/table/analysis/analysis_service.py::search()` 用
      `time.monotonic()` 给既有阶段打点（`scope_ms`/`count_ms`/`rows_ms`/
      `trend_ms`/`brand_probe_ms`），汇总 `db_total_ms`/`app_ms`/`total_ms`
- [x] 4.3 组装筛选白名单摘要（`_search_filter_summary`），超阈值时输出一条
      WARNING，含真实生效作用域（`site`/`view`/`on`）
- [x] 4.4 单元测试：`test/test_analysis_slow_query_unit.py`——人为让 `_fetch_trends`
      变慢触发诊断且字段齐全、不含检索词/ASIN 原文；未超阈值不输出；无数据早退
      路径**本身很快**时不输出（Codex 复审 PR #98 P2 之后修正：早退路径不再无条件
      跳过诊断——`_available_dates` 本身慢也会触发，见 4.6）
- [x] 4.5 核对不新增查询次数：`search()` 内新增的计时全部包裹既有
      `await db.execute(...)` 调用（`_stage()` 调用点前后没有插入新的查询），
      PR 描述里附这条核对说明
- [x] 4.6（Codex 复审 PR #98 P2 修复）`db_total_ms` 改由 `_timed_execute` 包裹每一次
      `await db.execute(...)` 单独累加，不再等于 `sum(stage_ms.values())`——原实现
      的阶段耗时里混着纯 Python 的结果整形（`shape_rows`/构造响应模型），导致
      `db_total_ms` 恒等于 `total_ms`、`app_ms` 恒等于 0，诊断从不指向应用侧。
      同时无可用日期的早退路径不再无条件跳过诊断——`_available_dates` 本身慢
      （连接池排队/锁等待）也会触发。回归测试见
      `test_app_side_shaping_time_not_misreported_as_db_time` 与
      `test_no_data_scope_but_slow_scope_resolution_still_diagnoses`

## 5. 排查说明文档与验证脚本（决策 E）

- [x] 5.1 完成 `etc/observability-runbook.md` 剩余内容：慢查询诊断读法、
      issue #4 复发时重启前的证据采集步骤（含 py-spy 要点）、日志局限说明
- [x] 5.2 `CLAUDE.md` 补一行指向 `etc/observability-runbook.md` 的引用
- [x] 5.3 新增 `scripts/check_logging.py`：多进程并发写入+模拟轮转、logrotate
      语法校验、request_id 关联三项检查；本地针对临时起的 uvicorn 实例（端口
      8010，验证后已停止）跑过，三项全过

## 6. 收尾

- [x] 6.1 `uv run ruff check .` —— 全部通过
- [x] 6.2 `uv run pytest test/ -q` —— 694 passed, 298 skipped（无 `TEST_DATABASE_URL`
      时集成测试跳过）；`TEST_DATABASE_URL=...keywordiq_test uv run pytest test/ -q`
      —— 989 passed, 1 skipped（本次新增的两个 slow-query 集成测试已在其中通过）
- [x] 6.3 `openspec validate add-request-diagnostics-logging --strict` —— 通过
- [x] 6.4 本文件勾选状态已更新；下方「生产待验收」清单尚未执行，如实保留未勾选

## 7. Codex 复审修复（PR #98）

- [x] 7.1（P1）`deploy.sh` 此前从不安装 `etc/keywordiq-logrotate.conf`——切到
      `WatchedFileHandler` 后应用侧不再做任何轮转，一次正常的容器化部署会让
      `logs/app.log` 无限增长直到打满磁盘，且没有任何报错。新增部署步骤
      "6/7 安装/更新 logrotate 配置"：替换模板里的占位路径为真实绝对路径、装进
      `/etc/logrotate.d/keywordiq`、`logrotate -d` 空跑校验语法，每次部署自动做
- [x] 7.2（P2）`etc/observability-runbook.md` 此前从未真正入库——写在 `docs/` 目录，
      而 `.gitignore` 明令整个 `docs/` 不入库（PRD/报价单/客户数据样本，参见该规则
      的注释）。移到 `etc/`（与本变更新增的另外两份运维模板同目录），`CLAUDE.md`/
      `proposal.md`/`design.md`/本文件的引用一并改路径
- [x] 7.3（P2）`app/table/analysis/analysis_service.py::search()` 的 `db_total_ms`/
      `app_ms` 见 4.6
- [x] 7.4（P2）`scripts/check_logging.py` 的 request_id 关联探测此前打 `GET /`
      并断言"回显等于发送值"——生产环境 `/` 由 nginx 的 SPA location 直接
      `try_files` 到 `index.html`，根本不转发到应用；即便改打 `/api/` 路径，
      nginx 按决策 C 会统一覆盖客户端自带的 `X-Request-Id`，回显值本就不该等于
      发送值。改打免鉴权的 `/api/auth/sso/status`，不再断言相等，只断言响应带回
      了某个 request_id 且那个值能在本地日志里查到
- [x] 7.5（P2）`scripts/check_logging.py` 的多进程轮转测试只用固定
      `time.sleep(0.05)` 赌轮转会撞上"worker 正在写"的窗口——200 行、仅 1ms 的
      期中停顿在正常速度的机器上会让 worker 全部写完再轮转，测试因此从未真正验证
      过"轮转发生在写入中途、下一次 `emit()` 能重新打开新文件"，只是凑巧靠"两段都
      落在同一个文件里"通过。改用 `Value` 计数器 + `Event` 让主进程精确同步到
      "全部 worker 写完前半段、正阻塞等待"这个窗口再执行轮转，并新增断言：轮转后的
      新文件必须确有记录（不能全部留在旧文件里）
- [x] 7.6（P2）`app/common/request_logging.py` 的 `error_before_response` 分支此前
      记 `status=None`——但 `error_handlers.py` 给裸 `Exception` 注册的兜底处理器
      会被 Starlette 拆到最外层的 `ServerErrorMiddleware`，在本中间件之外，异常能
      传播到这里就必然会被那个兜底处理器转成 500 发给客户端，`status=None` 会被
      误读成"客户端什么都没收到"。改为显式记 `status=500`，docstring 与
      `etc/observability-runbook.md` 的 outcome 说明一并更正
- [x] 7.7 回归测试：`test_app_side_shaping_time_not_misreported_as_db_time`、
      `test_no_data_scope_but_slow_scope_resolution_still_diagnoses`（均见 4.6）、
      `test_error_before_response_started_reraises` 补 `status=500` 断言；
      `scripts/check_logging.py` 本地对临时 uvicorn 实例（端口 8011）重新跑过
      全部三项，含 `/api/auth/sso/status` 探测，全部通过后已停止
- [x] 7.8 `uv run ruff check .` 全部通过；
      `TEST_DATABASE_URL=...keywordiq_test uv run pytest test/ -q` —— 993 passed,
      1 skipped（较修复前 989 passed 净增 4 个新增回归测试，无一失败）

## 8. 生产待验收（不在本次会话范围内，需人工执行并回填结果）

- [ ] 8.1 生产宿主机确认存在可用的 `logrotate` 定时触发机制（cron.daily 或等价）
      ——配置文件本身的安装已由 7.1 的 `deploy.sh` 步骤自动化，这里只需确认触发
      机制存在
- [ ] 8.2 在生产触发一次真实轮转（或等到自然轮转），核对轮转前后日志完整、
      `logs/app.log*` 保留数量符合预期
- [ ] 8.3 生产 `nginx -t` 校验新 `log_format` 配置语法后 reload，核对
      access log 出现 `request_time`/`upstream_time`/`request_id` 字段
- [ ] 8.4 生产环境验证一次 nginx 与应用日志按同一 request_id 关联成功
      （可用 `scripts/check_logging.py --base-url https://<生产域名>`，第 3 项现在
      打 `/api/auth/sso/status` 而不是 `/`；本地日志检索那部分需要在应用所在
      主机上跑才能核实）
- [ ] 8.5 核实生产磁盘余量在新增诊断日志量下的增长速率，确认现有磁盘监控阈值
      （见 CLAUDE.md「扩容触发条件」）仍然适用，不需要单独为日志新增磁盘告警
