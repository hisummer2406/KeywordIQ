## 1. 日志轮转（决策 A）

- [x] 1.1 `main.py::configure_logging()` 改用 `logging.handlers.WatchedFileHandler`
      替换 `TimedRotatingFileHandler`，日志格式加 `%(process)d`
- [x] 1.2 新增 `etc/keywordiq-logrotate.conf`（daily、rotate 7、compress、
      delaycompress、`create 644`，不用 `copytruncate`）
- [x] 1.3 单元测试：`WatchedFileHandler` 在文件被 rename+重建后，下一次 `emit()`
      能重新打开新文件并写入（`test/test_logging_config_unit.py`，用临时目录模拟
      rename，不依赖真实 logrotate）
- [x] 1.4 `docs/observability-runbook.md` 起草，先写第 1/2 节骨架（关联本组产出，
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
      路径不输出
- [x] 4.5 核对不新增查询次数：`search()` 内新增的计时全部包裹既有
      `await db.execute(...)` 调用（`_stage()` 调用点前后没有插入新的查询），
      PR 描述里附这条核对说明

## 5. 排查说明文档与验证脚本（决策 E）

- [x] 5.1 完成 `docs/observability-runbook.md` 剩余内容：慢查询诊断读法、
      issue #4 复发时重启前的证据采集步骤（含 py-spy 要点）、日志局限说明
- [x] 5.2 `CLAUDE.md` 补一行指向 `docs/observability-runbook.md` 的引用
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

## 生产待验收（不在本次会话范围内，需人工执行并回填结果）

- [ ] 7.1 生产宿主机确认存在可用的 `logrotate` 定时触发机制（cron.daily 或等价），
      并把 `etc/keywordiq-logrotate.conf` 装入 `/etc/logrotate.d/keywordiq`
      （部署前需把配置里的 `/path/to/keywordiq/logs/app.log` 占位符改成实际路径）
- [ ] 7.2 在生产触发一次真实轮转（或等到自然轮转），核对轮转前后日志完整、
      `logs/app.log*` 保留数量符合预期
- [ ] 7.3 生产 `nginx -t` 校验新 `log_format` 配置语法后 reload，核对
      access log 出现 `request_time`/`upstream_time`/`request_id` 字段
- [ ] 7.4 生产环境验证一次 nginx 与应用日志按同一 request_id 关联成功
      （可用 `scripts/check_logging.py --base-url https://<生产域名>`，但第 3 项的
      本地日志检索那部分需要在应用所在主机上跑才能核实）
- [ ] 7.5 核实生产磁盘余量在新增诊断日志量下的增长速率，确认现有磁盘监控阈值
      （见 CLAUDE.md「扩容触发条件」）仍然适用，不需要单独为日志新增磁盘告警
