## Why

现状排查一次线上请求几乎只能靠"报错了才有日志"：`Dockerfile` 用 `--no-access-log` 关掉了
uvicorn 的访问日志，`main.py` 也没有请求摘要中间件，一次**成功但很慢**的请求在日志里完全没有
痕迹；`app/table/analysis/analysis_api.py::search_data`（分析查询屏主查询）走的是异步引擎，
而 `database.py` 里唯一的慢查询事件监听只挂在同步引擎上——对这条最高频的读路径形同虚设；
nginx 侧（`etc/keywordiq-nginx.conf`）没有自定义 `log_format`，无法把一次请求在 nginx 与
应用日志之间用同一个编号关联，代理层的 502/504 也查不到编号；`main.py` 的
`TimedRotatingFileHandler` 由 4 个独立 uvicorn worker 各自实例化、共同指向同一个物理文件
（`logs/app.log`），这是 Python 官方文档明确警告的非多进程安全模式，午夜轮转时有相互踩踏、
丢日志的风险，且从未验证过。

issue #4 的导入期卡死至今根因未证实；#90（运维巡检/告警）需要的日志基础目前也不存在。本变更
不解决 #4、不做 #90 的巡检与告警，只把维护者"从一次请求定位失败或慢在哪里"所需的日志基础
补齐，并确认日志在轮转、重启后仍可检索。

本次改动只影响应用层与部署配置（nginx、logrotate、日志格式），**不涉及日报/周报导入路径**，
两条数据路径均不改动。

## What Changes

- 修复 4-worker 共享 `TimedRotatingFileHandler` 的轮转竞争：改用
  `logging.handlers.WatchedFileHandler`（无内建轮转逻辑，按文件 dev/inode 变化自动重新打开），
  轮转交给宿主机 `logrotate`（新增 `etc/keywordiq-logrotate.conf` 模板，`create` 模式、
  不用 `copytruncate`）。日志格式补 `%(process)d` 辅助多进程定位。
- 新增请求摘要中间件：每个请求结束时记录一条 method/path（不含 query string）/status/
  总耗时/request_id 的摘要，区分 `completed`（正常发完）/`interrupted`（响应头已发但客户端
  断连或任务取消、body 未发完，且没有异常）/`error`（响应头发出后才抛出的未处理异常）三种
  结束形态，覆盖流式导出的中途失败/断连场景；健康检查 `/health` 不计入。
- nginx 侧新增自定义 `log_format`，记录 `$request_time`/`$upstream_response_time`/
  `$upstream_status`/`$request_id`，并把 `$request_id` 通过 `X-Request-Id` 头下发给应用——
  nginx 成为该编号的唯一权威生成者，与应用现有的 `RequestIdMiddleware`（沿用入站头）天然衔接，
  代理层失败（502/504）也有可检索的编号。
- 修复慢查询事件监听的覆盖缺口：把 `before_cursor_execute`/`after_cursor_execute` 同时挂到
  `async_engine`（此前只挂同步引擎，异步查询路径完全没有慢查询记录），阈值从硬编码改为可配置
  的 `SLOW_QUERY_THRESHOLD_SECONDS`。
- 新增 `/api/analysis/search` 专属慢查询结构化诊断：总耗时超过可配置阈值
  （`ANALYSIS_SLOW_QUERY_THRESHOLD_MS`）时，追加输出已解析的生效作用域（站点/视图/锚定日）、
  分页/排序、筛选维度的白名单摘要（布尔/计数字段，不含检索词或 ASIN 原文）、以及既有执行阶段
  （可用日期、计数、取行、趋势回溯、品牌探测）的分段耗时 + 应用处理耗时；不新增查询次数、
  不在阈值以下时输出。
- 新增排查说明文档 `etc/observability-runbook.md`：如何用 request_id 关联 nginx/应用日志、
  如何读慢查询诊断、issue #4 类导入期卡死复发时重启前需要采集的证据（py-spy 栈等）、以及
  "请求完成后的日志"与"进程被杀/尚未走到日志点"的局限区分。
- 新增 `scripts/check_logging.py`：仿照 `scripts/check_oss.py`/`check_dingtalk_sso.py` 的
  惯例，实测多进程并发写日志 + 触发轮转不丢失、request_id 跨层可关联，这两项无法只靠代码走查
  确认。

**不在本次范围**：不引入 ELK/SLS/消息队列，不建长期指标平台或自动告警（#90 范围），不改
连接池参数或修 #4 的假定根因，不做自动生产发布，不建用户行为轨迹/埋点表。生产环境实际磁盘
余量、日志出口核验、logrotate 在生产真实生效、真机 nginx 配置生效验证需要生产服务器访问，
本次列为待验收项，不宣称已完成。

## Capabilities

### New Capabilities
- `request-diagnostics-logging`：请求可观测性基础——多进程安全的日志轮转、跨 nginx/应用的
  请求关联与结束形态区分、慢查询定位（通用 SQL 级 + 分析查询屏专属结构化诊断）。

### Modified Capabilities
（无。响应契约本身不变——`api-error-contract` 的失败响应形状、状态码语义、流式端点的错误
处理纪律均不改动，本次只新增服务端日志行为，不改变任何 API 对外可观察的响应内容。）

## Impact

- **代码**：`main.py`（日志配置、请求摘要中间件挂载）、`app/common/request_context.py`
  （如需扩展 send 包装逻辑）、`database.py`（async_engine 慢查询监听、阈值改为配置）、
  `config.py` + `.env.example`（新增 `SLOW_QUERY_THRESHOLD_SECONDS`、
  `ANALYSIS_SLOW_QUERY_THRESHOLD_MS`）、`app/table/analysis/analysis_service.py::search`
  （阶段计时与诊断日志）、新增 `app/common/request_logging.py`（请求摘要中间件实现）。
- **部署配置**：`etc/keywordiq-nginx.conf`（log_format + X-Request-Id 下发）、新增
  `etc/keywordiq-logrotate.conf`。
- **文档**：新增 `etc/observability-runbook.md`；CLAUDE.md 视需要补一条指向该文档的引用。
- **脚本**：新增 `scripts/check_logging.py`。
- **测试**：新增/扩展单元测试覆盖请求摘要中间件的三种结束形态、慢查询诊断的字段白名单与
  阈值行为、日志格式改动不破坏 `RequestIdLogFilter` 现有契约。
- **不影响**：日报/周报导入路径、数据模型、前端。
