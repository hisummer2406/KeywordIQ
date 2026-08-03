## ADDED Requirements

### Requirement: 查询与鉴权路径在事件循环上异步执行

面向请求的读路径（分析查询、作用域、类目、鉴权、用户管理）MUST 声明为 `async def`
并使用异步会话（`AsyncSession` / asyncpg）。这些路径 MUST NOT 使用同步 `Session`。

理由：这是唯一随用户数增长的路径（约 200 个运营的全部读操作）。同步会话在
`async def` 中执行会阻塞事件循环，使该 worker 在此期间无法服务任何其他请求。

#### Scenario: 分析查询端点使用异步会话
- **WHEN** 调用 `POST /api/analysis/search`、`GET /api/analysis/scope` 或
  `GET /api/analysis/categories`
- **THEN** 端点以 `async def` 声明，其数据库访问经由异步会话 `await` 完成

#### Scenario: 面向请求的路径不得持有同步会话
- **WHEN** 检查任一 `async def` 端点及其调用链
- **THEN** 其中不存在同步 `Session` 或 `SessionFactory` 的使用

### Requirement: 事件循环不得被 CPU 密集操作阻塞

在事件循环上执行的代码 MUST NOT 直接调用耗时显著的 CPU 密集操作。此类操作
MUST 经线程池执行（`run_in_threadpool` 或等价手段）。

理由：异步化解决的是 I/O 等待，解决不了 CPU 占用。密码哈希实测单次约 247 ms，
直接在事件循环上调用会使该 worker 停摆同等时长——集中登录时段可被用户感知。

#### Scenario: 密码校验不阻塞事件循环
- **WHEN** 调用 `POST /api/auth/token` 执行登录
- **THEN** bcrypt 的校验在线程池中执行，事件循环在此期间可继续处理其他请求

#### Scenario: 密码哈希不阻塞事件循环
- **WHEN** 创建用户或修改密码触发密码哈希
- **THEN** bcrypt 的哈希在线程池中执行

### Requirement: 批处理路径保持同步且理由明确

导入装载、导出流式产出与数据库迁移 MUST 继续使用同步引擎。任何把这些路径改为
异步的改动 MUST 先证明其功能等价。

理由不是偏好而是硬约束：导入的 `COPY ... FROM STDIN` 依赖 psycopg2 的原始游标，
asyncpg 无功能等价的接口；这三条路径都是低并发长事务，异步化没有可获得的收益。

#### Scenario: 导入继续以 COPY 流式装载
- **WHEN** 执行任一站点、任一周期（日报或周报）的导入
- **THEN** 装载经同步引擎的原始游标以 `COPY` 完成，内存占用与文件大小无关

#### Scenario: 导出继续以服务端游标流式产出
- **WHEN** 调用 `POST /api/analysis/export` 且行数在上限内
- **THEN** CSV 经同步会话的服务端游标分块产出，该会话由流式生成器自建并自行关闭

### Requirement: 连接池按用途分开配置

异步引擎与同步引擎 MUST 使用各自独立的池容量配置项，MUST NOT 共用同一组取值。

理由：两个引擎服务的负载性质完全不同——异步池承载全部面向请求的查询，同步池
只承载单线程的批处理。共用一组取值曾使全局上限的算式被系统性低估一半
（文档写 `(池 + 溢出) × workers`，实际是 `2 × (池 + 溢出) × workers`）。

#### Scenario: 异步池与同步池可独立调整
- **WHEN** 调整异步池容量
- **THEN** 同步池容量不受影响，反之亦然

#### Scenario: 配置项在示例文件中完整登记
- **WHEN** 检查 `.env.example`
- **THEN** 同步与异步两组池配置项均在列，并注明各自服务的路径与全局上限算式

### Requirement: 全局连接上限可验算且留有余量

连接池取值 MUST 满足：所有 worker、所有引擎的连接数之和在满载时仍显著低于
数据库的 `max_connections`，并为数据库自身的管理连接与人工排查连接留出余量。
该算式 MUST 在配置注释与项目文档中写明，且 MUST 计入每个 worker 创建的引擎数量。

#### Scenario: 满载上限低于数据库允许值
- **WHEN** 按 `引擎数 × (池 + 溢出) × worker 数` 计算满载连接上限
- **THEN** 结果显著低于 RDS 的 `max_connections`（400），并留有管理连接余量

#### Scenario: 文档中的算式与实际引擎数一致
- **WHEN** 对照配置注释、`.env.example` 与 CLAUDE.md 中的连接数算式
- **THEN** 三处一致，且算式中的引擎数与 `database.py` 实际创建的引擎数相符

### Requirement: 应用侧并发上限不超过可用连接

处理同步端点的线程池上限 MUST NOT 超过同步连接池的满载容量。

理由：线程数超过可用连接时，多出的线程会在连接池上等待直至 `pool_timeout` 超时报错。
把并发限制在应用侧，排队快、可观测，且不消耗数据库连接。

#### Scenario: 突发请求以排队而非报错方式降级
- **WHEN** 并发请求数超过连接池容量
- **THEN** 超出部分在应用侧排队等待，不产生连接池超时错误
