## 1. 配置拆分（行为不变）

- [x] 1.1 `config.py` 新增 `DB_ASYNC_POOL_SIZE` / `DB_ASYNC_MAX_OVERFLOW`，默认值先等于当前生效值（20 / 10），使本步不改变任何运行行为
- [x] 1.2 `database.py` 的 `async_engine` 改用异步池配置项，`engine` 保留原配置项；两处注释写明各自服务哪条路径
- [x] 1.3 `.env.example` 补齐两组配置项，注明服务对象与全局上限算式 `引擎数 × (池 + 溢出) × workers`
- [x] 1.4 CLAUDE.md 的连接池算式同步更新，与 `.env.example`、`config.py` 三处一致
- [x] 1.5 确认 `test_conventions.py` 的配置一致性与死配置两条规则对新增项均通过（新配置项不得成为死配置）

## 2. analysis_crud 拆分构造与执行

- [ ] 2.1 把 `available_dates` / `scope_row_count` / `search_rows` / `count_capped` / `count_filtered` / `iter_export_rows` / `fetch_trends` / `fetch_week_changes` 各拆为「纯构造函数（返回 statement）」与「纯整形函数（接收 rows）」，执行留给调用方
- [ ] 2.2 保持 `_base_query` / `_apply_filters` / `_apply_order` / `request_has_filters` 不变（已是纯函数）
- [ ] 2.3 在同步路径上先接回拆分后的函数，跑 `test_site_scoped_query_integration.py` 与 `test_analysis_export_integration.py`，确认纯重构零行为变化

## 3. 查询路径异步化

- [ ] 3.1 `analysis_service` 的 `search` / `scope` / `categories` 三条路径改为异步，用 `AsyncSession` 执行第 2 步拆出的语句；作用域解析与 `ScopeError` 逻辑不变
- [ ] 3.2 `analysis_api` 的 `POST /search`、`GET /scope`、`GET /categories` 改 `async def` + 异步会话依赖
- [ ] 3.3 `POST /export` 保持 `def` 与同步会话；确认它依赖的异步鉴权依赖能正常解析
- [ ] 3.4 确认日视图的走势与当周排名变化（`fetch_trends` / `fetch_week_changes`）在异步路径下结果与同步路径一致，尤其是同 ISO 周映射口径
- [ ] 3.5 让既有集成测试的断言在异步路径下重跑（不新写宽松断言），补异步测试夹具

## 4. 鉴权与用户管理异步化

- [ ] 4.1 `simple_auth` 的 `get_current_user` / `require_permission` / `login` 改用 `AsyncSession`
- [ ] 4.2 bcrypt 的 `hash_password` / `verify_password` 经 `run_in_threadpool` 调用，确保不在事件循环上执行
- [ ] 4.3 `user_crud` 改异步；保持上一变更确立的错误契约（失败抛 `APIError`，读路径不吞异常）
- [ ] 4.4 `auth_api` / `user_api` 端点改用异步会话依赖
- [ ] 4.5 排查 `AsyncSession` 下的隐式懒加载：确认所有 ORM 属性访问都在会话内或已预加载（`role_code` 等跨表属性重点检查）
- [ ] 4.6 `test_rbac_auth_unit.py`、`test_user_error_contract_unit.py`、`test_protable_contract.py` 的夹具改异步，断言不放宽

## 5. 收口与一致性

- [ ] 5.1 `/oss/processing-status` 已用异步会话，确认与新的会话依赖写法一致
- [ ] 5.2 `main.py` 的 lifespan 探活与 `/health` 统一到新的引擎划分；顺带确认 `/health` 失败时不再把 `str(e)` 序列化进响应体
- [ ] 5.3 全仓搜索确认面向请求的 `async def` 调用链中不再存在同步 `Session` / `SessionFactory`
- [ ] 5.4 线程池上限对齐同步池容量（在应用启动处设置 AnyIO 限流器）

## 6. 验证

- [ ] 6.1 `uv run ruff check .` 全绿；提交门禁在每一步提交时均放行
- [ ] 6.2 全量 `TEST_DATABASE_URL=... uv run pytest test/ -q` 通过，集成测试未被静默跳过（skip 数应为 1）
- [ ] 6.3 实测登录延迟：确认 bcrypt 期间同一 worker 仍能处理其他请求（并发发起一次登录与若干查询，查询不被阻塞约 250 ms）
- [ ] 6.4 用真实体量数据核对查询结果与异步化前逐字段一致（走 `verify-import` skill 备好数据后对比）
- [ ] 6.5 确认迁移可回滚性不受影响（`alembic downgrade -1 && upgrade head` 仍走同步引擎）

## 7. 上线与调参（配置值最后动）

- [ ] 7.1 前六组完成后先按「取值不变」部署，验证异步化本身无回归
- [ ] 7.2 观察一个业务周期的 `pg_stat_activity`：记录 `active` 峰值与 `idle in transaction` 是否堆积
- [ ] 7.3 据实测落定连接池取值（设计建议异步 8+8、同步 2+3，全局上限 84），改 `.env` 重启，不需代码回滚
- [ ] 7.4 回填实测数据到 design.md 的开放问题（线程池上限取值、导出是否值得异步化）
