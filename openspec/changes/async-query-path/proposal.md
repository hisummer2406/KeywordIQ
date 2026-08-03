## Why

请求路径的执行模型现在是拧反的：**流量最大的查询路径跑同步，声明成异步的反而在事件循环上做阻塞操作。**

- `analysis_api` 的 4 个查询端点（约 200 个运营的全部读操作）是普通 `def`，走 AnyIO 线程池，接的是同步 psycopg2 引擎。
- `auth_api.issue_token`、`simple_auth.login` 与 `user_api` 的 5 个端点声明为 `async def`，却在事件循环上执行同步 `Session` 查库并调用 bcrypt。实测 `bcrypt.verify` 单次 **247 ms** 纯 CPU——每次登录都让该 worker 停摆约 0.25 秒，期间无法服务任何查询。早班集中登录时这段停摆可感知。

同时连接池配置建立在一个算错的前提上：`database.py` 在模块级创建同步与异步**两个**引擎、共用同一组 `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`，全局上限是 `2 × (池 + 溢出) × 4 workers = 240`，而不是文档长期以来所写的 80。240 条连接对 2 vCPU 的 RDS 无意义——PostgreSQL 同时真正能执行的查询受核心数限制，约 4 个；多出来的只会在库内排队。更糟的是异步引擎那 120 条额度几乎闲置：它当前只服务健康检查与导入状态轮询（单个数据专员在用）。

**为什么是现在**：查询层（site-scoped-query）已落地稳定，此时调整执行模型不会与在飞的功能开发冲突；再往后接入更多查询屏，改动面只会更大。

## What Changes

- **查询路径异步化**：`analysis_api` 的 `POST /search`、`GET /scope`、`GET /categories` 改为 `async def` + `AsyncSession`（asyncpg），`analysis_crud` 的查询组装改为可等待。
- **鉴权与用户管理真异步**：`auth_api` / `simple_auth` / `user_api` 改用 `AsyncSession`；**bcrypt 的哈希与校验移出事件循环**（`run_in_threadpool`），因为它是 CPU 密集，异步化不能解决阻塞，只能移走。
- **同步引擎收缩到批处理用途**：导入（`COPY` 需要 psycopg2 原始游标，无异步等价物）、导出（服务端游标流式产出）、Alembic 迁移保持同步，不改。
- **连接池按用途分开配置**：新增 `DB_ASYNC_POOL_SIZE` / `DB_ASYNC_MAX_OVERFLOW`；异步池服务查询（建议 8+8，每 worker 16、全局 64），同步池服务批处理（建议 2+3，每 worker 5、全局 20）。全局上限从 240 降到 84。**BREAKING**：新增必填/带默认的配置项，`.env` 与部署环境需同步。
- **线程池上限与池容量对齐**：避免线程数超过可用连接、把等待变成 `pool_timeout` 报错。
- 文档修正：`.env.example`、CLAUDE.md 中的连接数算式写明「两个引擎各占一份池」。

**不做**（明确排除，避免范围蔓延）：不改任何查询语义、不动作用域规则与排序白名单、不改 ProTable 契约、不动导入管线、不引入 pgbouncer。

## Capabilities

### New Capabilities
- `async-request-execution`: 请求路径的执行模型。规定哪些路径必须异步、哪些必须同步及其硬性理由；规定事件循环不得被同步 I/O 或 CPU 密集操作阻塞；规定连接池按用途分配且全局上限有可验算的算式并留有余量。

### Modified Capabilities
<!-- 无。本变更不改变任何对外可观察的查询行为：作用域规则、排序白名单、
     ProTable 契约、日期选择器口径、导出上限均不变，site-scoped-query 与
     api-error-contract 的既有需求原样成立。 -->

## Impact

**影响日报路径与周报路径两者**——查询层的 DAY / WEEK 两个视图共用同一批端点与同一个引擎，异步化对二者同时生效。**导入侧日报与周报均不受影响**，两条导入路径都继续走同步 `COPY`。

受影响代码：

- `app/table/analysis/analysis_api.py`、`analysis_service.py`、`analysis_crud.py`（查询路径；导出所用的自建会话保持同步）
- `app/auth/auth_api.py`、`app/auth/simple_auth.py`
- `app/user/user_api.py`、`app/user/user_crud.py`
- `app/table/upload/upload_api.py` 的 `/processing-status`（已是异步，随之统一）
- `database.py`（引擎与池参数分离）、`config.py`、`.env.example`、`main.py`（lifespan 探活与健康检查）
- `test/` 中依赖同步 `Session` 的相关用例需改用异步夹具

不受影响：`importer.py`、`import_service.py`、`retention_service.py`、`partitioning.py`、`alembic/`。

依赖：`asyncpg` 与 `sqlalchemy[asyncio]` 已在依赖清单中，无需新增第三方包。

运维：连接池取值变化会改变生产行为，上线后需观察 `pg_stat_activity` 的 `active` 峰值与 `idle in transaction` 堆积情况，据实测回调。
