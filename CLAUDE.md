# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KeywordIQ - A professional Amazon search term data analysis platform supporting GB-level file processing, real-time data analysis, and visualization. Built with FastAPI + PostgreSQL, serving ~200 concurrent users for read-heavy operations with periodic batch data imports.

**Import data volumes (critical for performance decisions):**
- Daily report: ~300K rows, ~150MB CSV, imported once per day
- Weekly report: ~3M rows, ~1.5GB CSV, imported once per week
- The 10× size difference means daily and weekly imports have fundamentally different performance characteristics and must be treated as separate problem domains

## Development Commands

```bash
# Local development (uv manages .venv per .python-version + uv.lock)
uv sync
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest test/

# Add a dependency
uv add <package>          # runtime
uv add --dev <package>    # dev-only

# Production deployment (host nginx + uvicorn; no containers)
./deploy.sh                            # Full release: uv sync → migrate → build SPA → publish → reload nginx
./deploy.sh --skip-migrate             # Frontend-only release
./start.sh                             # Start backend directly (4 uvicorn workers)
tail -f logs/app.log | grep -i import  # Monitor import logs

# Database operations
# Uses PostgreSQL with schema "analysis"
# Connection: sync (psycopg2) + async (asyncpg) engines both configured

# Database migrations (Alembic)
uv run alembic upgrade head          # 应用迁移
uv run alembic downgrade -1          # 回滚一步
uv run alembic upgrade head --sql    # 只生成 SQL，不连库（离线预览）
uv run alembic revision -m "说明"     # 新建空迁移（分区表/物化视图必须手写）
uv run alembic revision --autogenerate -m "说明"  # 从模型差异生成
uv run alembic current               # 当前版本
```

## V2 数据模型（建设中）

V1 已完成使命并退役，V2 是完整重建。当前进度：数据模型（地基）已落地，导入、查询、
界面、归档四个层次的重写尚未开始。规划文档见 `openspec/changes/add-multi-site-data-model/`。

**V2 的表**（星型模型，全部在 `analysis` schema 下）：

| 表 | 说明 |
|---|---|
| `dim_keyword` | 关键词维度，`(site, keyword)` 唯一；GIN 全文索引建在这里 |
| `dim_product` | 商品维度，`asin` 唯一；SCD Type 1（不留历史版本） |
| `fact_daily` / `fact_weekly` | 事实表，按 `report_date` 月度 RANGE 分区 + 按 `site` 二级 LIST 分区 |
| `site_data_stats` | 按站点/周期/期次的行数，服务分页总数与日期选择器 |
| `sites` / `import_batch` | 站点参考表 / 导入批次记录 |

**三条不可违反的约束**：

1. **事实表只追加，不原地更新。** 排名、上期排名、排名变化、是否新词都在写入时算定。
   这是按月归档成立的前提，也是死元组归零的原因。唯一例外是同一期重复导入（先删该
   切片再写）。
2. **写事实表前必须调用 `app.table.analysis.partitioning.ensure_fact_partitions`。**
   分区缺失会让导入直接报错，该函数是幂等的，无条件调用即可。
3. **站点码的唯一事实来源是 `app/site_registry.py`。** 不要在别处硬编码站点列表。
   英国是 `GB` 不是 `UK`。

**迁移注意**：分区表与物化视图不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把
事实表产成普通表并静默丢掉分区。涉及这两类对象的迁移必须手写，并用
`alembic upgrade head --sql` 核对生成的 DDL 里确有 `PARTITION BY`。

**V2 查询层（site-scoped-query，已落地）**：分析查询查 V2 星型模型，作用域是
「站点 + 视图（DAY/WEEK）+ 日期」三元组——任一查询只落在单一站点、单一视图、单一日期的
事实切片，筛选/排序/分页 MUST NOT 突破该边界。要点：

- **日期选择器与分页总数走 `site_data_stats`**（不在 8 亿行上实时聚合）；类目下拉走
  `category_stats` 物化视图。查询代码在 `app/table/analysis/analysis_crud.py`（纯 SQL 组装）
  → `analysis_service.py`（作用域解析、`ScopeError`）→ `analysis_api.py`
  （`POST /search`、`GET /scope`、`GET /categories`，均 `require_permission(analysis:view)`；
  另有 `POST /export`，权限点是 `analysis:export`）。
- **导出**（`POST /api/analysis/export`）与 `/search` 共用请求体，导出的就是用户当下看到的那份
  数据（分页参数忽略）。CSV 经服务端游标（`crud.iter_export_rows` 的 `yield_per`）分块流式产出，
  内存与块大小同阶；流式生成器**自建会话**（`SessionFactory`），因为响应体在依赖清理之后才被消费，
  复用请求会话会踩「会话已关闭」。行数超 `EXPORT_ROW_CAP`（默认 5 万）**拒绝**（413），
  不静默截断——截断的 CSV 会被当成完整数据用。上限检查必须在开始流式输出之前做完。
- **类目筛选是多选**：请求字段 `categories`（列表，命中其一即可，上限 100），空列表视同不筛。
  没有单值的 `category_exact`——它在 `redesign-analysis-filter-layout` 里被替换掉了。
- **默认站点** `DEFAULT_SITE`（config，须属站点清单）；未传站点用它、非法站点报错**不回退**。
- **日视图**附带只读的近 7 天走势与各点日期（`fetch_trends`/`trends_stmt`，「先分页再批量
  补附加」）。**排序白名单**按视图，日视图不许按走势排序。
  **日视图不再附带「当周排名变化」**——它把周报数据混进日报视图，让「这一行属于哪份数据」
  变得含糊，还要求查询侧自行定义周边界（`date_trunc('week')`），而导入侧只存文件名日期、
  不定义周边界。要看周维度的变化就切到周视图。
- 列表响应走 ProTable 契约 `{data,total,success}` + 作用域回显。集成测试
  `test/test_site_scoped_query_integration.py`（`TEST_DATABASE_URL` gate）。
- 演示数据：`scripts/seed_demo_data.py`（连续 7 天 + 对齐周报，让走势可见，仅本地）。

**数据保留：只保留最近三个月。** 由 `HOT_WINDOW_MONTHS`（默认 3）定义热窗口，报告日期
早于窗口下界的月度分区由保留清理**自动移出**——日报与周报统一适用同一窗口。移出是整
分区 `DROP`（连带站点子分区），不逐行删除、不产生死元组、不影响窗口内其他月份。清理逻辑
在 `app/table/analysis/retention_service.py`，运维入口是可定时触发的 `scripts/prune_retention.py`
（cron / 容器定时任务在低峰调用，务必先 `--dry-run` 核对、可 `--export-dir` 留档）。
**注意**：超过三个月的历史数据不再可在线查询（相对早期"12 个月满窗"是行为收窄）。

**扩容触发条件**（数据从上线起约 6600 万行/月、16 GB/月增长）：磁盘使用率超过 70%，
或缓存命中率（`blks_hit / (blks_hit + blks_read)`）持续低于 95%。在三个月保留窗口下满窗
约 2 亿行 / 50 GB，4C16G / RDS 2C4G 足以稳定承载，通常无需扩容；若保留窗口调大或数据
增速超预期而触发上述阈值，再评估扩到 8C32G。

## Architecture

### Module Structure

```
app/
├── auth/              # 自建 JWT + RBAC（simple_auth.py: 登录/get_current_user/require_permission）
├── site_registry.py   # 站点码唯一事实来源（英国是 GB 不是 UK）
├── table/
│   ├── analysis/      # V2 查询层（model, crud, service, api + retention/partitioning）
│   ├── search/        # 查询请求/响应 schemas
│   └── upload/        # OSS 直传凭证 + 三段式导入（oss_client / import_source / importer / import_service）
└── user/              # 用户与角色管理 (model, crud, api)

main.py                # FastAPI app entry, lifespan management, health check
config.py              # Pydantic settings (env-based config)
database.py            # SQLAlchemy 2.0 sync + async engines, session factories
frontend/              # AntD Pro SPA（Umi Max），独立构建，产物由 nginx 托管
```

### Layered Architecture Pattern

**All table modules follow strict layering:**

1. **Model** (`*_model.py`): SQLAlchemy 2.0 ORM models using `DeclarativeBase`, schema-qualified
2. **CRUD** (`*_crud.py`): Database access layer, no business logic
3. **Service** (`*_service.py`): Business logic, calls CRUD layer
4. **API** (`*_api.py`): FastAPI routers, thin controllers calling services
5. **Schemas** (`*_schemas.py`): Pydantic v2 models for request/response DTOs

**Example**: `app/table/analysis/` - `analysis_api.py` → `analysis_service.py` → `analysis_crud.py` → `analysis_model.py`

### Data Import Flow

**V2 OSS-first 导入（现行，日报周报同一套流程）**：表格文件**先直传 OSS，再从 OSS 导入**。

1. **签发直传凭证** `POST /oss/upload-credentials`（`upload_api.py` → `oss_client.issue_upload_credentials`）：
   后端调 STS `AssumeRole` 换取受限临时凭证（RAM 策略限死到指定 bucket + object key 前缀 + 仅写
   操作 + 短有效期），前端凭此**直传 OSS**，GB 级字节不经应用服务器。服务端凭证只在后端持有。
2. **触发导入** `POST /oss/import`：前端回传 object key；后端校验 key 落在授权前缀内、对象存在，
   再交后台任务 `run_import`。
3. **三段式导入**（`importer.py`，源经 `ImportSource` 抽象）：从 OSS 对象**流式** `COPY` → UNLOGGED
   暂存表 → 补齐维度 → 关联上一期算派生值、**追加**写事实分区（写前必调 `ensure_fact_partitions`）。
   内存有界，不整文件落地。
4. **OSS 对象即原始归档**：文件入口即持久化于 OSS，不再做导入后本地归档；批次记录（`import_batch.object_key`）
   持久化 object key 供溯源与重导。失败也无碍——OSS 对象仍在，凭 key 重导。

**object key 约定**：`<OSS_KEY_PREFIX>/<SITE>/<PERIOD>/<report_date>/<uuid>_<原文件名>`，
如 `imports/US/DAY/2026-07-18/ab12cd34_US_..._Day_2026_07_18.csv`。前缀由服务端生成并写进凭证策略，
前端无法越权到其他站点/期次；`report_date` 只来自文件名（分区归属的权威来源）。

**配置**：`OSS_*`（见 `.env.example`）；`OSS_ENABLED=false` 时走本地文件退路
（`LocalFileSource` + `POST /import-csv`，不归档），供本地开发与 CI。配好后用
`uv run python scripts/check_oss.py` 验证整条链路——它按前端直传的真实顺序跑一遍
（STS 签发 → 临时凭证直传 → **越权写入必须被拒** → 服务端流式读回 → bucket CORS），
退出码非 0 即有项未通过。其中"越权被拒"与 CORS 两项无法靠 code review 确认，只能实测。

**bucket 的 CORS 不在本仓库配置范围内，但直传依赖它**：来源需含前端域名、方法需含
`PUT` 与 `POST`（分片上传的 Initiate/Complete 走 POST）、**暴露头必须含 `ETag`**——
ali-oss 要读每片的 ETag 才能 `CompleteMultipartUpload`，缺它的表现是字节全传完了、最后
一步失败，极易误判成网络问题。

**上线前必改**（开发环境放宽处，清单与理由见 `.env.example` 的 OSS 段）：CORS 来源从 `*`
收窄到实际域名、`OSS_ENDPOINT` 换内网地址、生产用独立 bucket 与独立 RAM 子账号。

**导入串行**：`pg_try_advisory_lock` 保证同一时刻只有一个导入在跑（2C4G 上两个 GB 级导入并发会争抢
WAL 与索引构建），拿不到锁的批次置 `QUEUED`。

> V1 的 `csv_processor.py`（pandas 分块 + `ON CONFLICT (keyword)` UPSERT + `ranking_trend_day` JSONB
> 7 天走势）随 V1 `/upload-csv` 端点一同退役，不要在 V2 路径复用。

### Authentication & Authorization

- **自建 JWT + RBAC**（`fastapi-user-auth`/`fastapi-amis-admin` 已随 amis 于阶段七移除）。
  用户模型 `UserCenter`（`user_center` 表）、`passlib` bcrypt 密码、`AuthService`（PyJWT/HS256/
  `ADMIN_SECRET_KEY`）、`simple_auth`（登录、`get_current_user`、`require_permission`）。
- **RBAC**：`role`/`permission`/`role_permission`（M2M），`user_center.role_id`（每用户一角色）。
  权限点 `analysis:view`/`analysis:export`/`upload:import`/`user:manage`/`role:manage`；内置角色
  admin/operator/analyst（见 `app/auth/permissions.py`）。逐路由 `require_permission(code)` 授权，
  is_super 兜底。前端在 `access.ts` 按权限码显隐菜单。
- **端点**：`POST /api/auth/token`（登录发令牌，免鉴权）、`GET /api/auth/me`、
  `POST /api/auth/logout`（干净 REST）。
- **鉴权显式落在每个端点的依赖上**（`Depends(require_permission(code))` 或 `get_current_user`），
  没有全局鉴权中间件——旧的 `AdminAuthMiddleware` 因 `exclude_paths` 含 `"/"` 形同虚设，已删除。
  新增受保护端点务必挂鉴权依赖（无全局兜底）。`test/test_protable_contract.py` 有"无 token 必拒"回归。
- 初始管理员：`scripts/seed_admin.py`（默认 `admin / pwd123`；生产改 `ADMIN_SECRET_KEY`）。

**后台 UI 是独立的 AntD Pro SPA**（`frontend/`，Umi Max + Ant Design + ProComponents），
本服务只提供 JSON API（amis 后台已删）。生产由 nginx 托管 SPA 产物 + 反代 `/api`。

**分析查询屏（`frontend/src/pages/Analysis/`）分三段**：顶部作用域栏（站点/视图/日期/检索框/
立即查询）、中部过滤区（左类目多选、右结果过滤）、底部 ProTable（`search={false}`）。三条约定：

- **查询显式触发**。条件改动只进草稿态，点「立即查询」才生效（`draft` 与 `applied` 是**两份**
  状态，不是一份加脏标记——否则「表格现在显示的是哪组条件」无从读出，导出会跟着漂移）。
  两个例外：排序/翻页走 ProTable 自身 `request` 即时生效；改站点或视图立即重拉 `/scope` 与
  `/categories`（小表）但**不**重查表格，否则用户会在属于上一个站点的日期清单里做选择。
- **一个筛选维度只有一个入口**。关键词与 ASIN 归顶部检索框（按 `^B0[A-Z0-9]{8}$` 判定走哪个
  字段，判定结果必须回显），过滤区不再重复提供。
- **份额是 0~100 的百分数**，界面、快捷档位、接口一路不做换算；**排名变化保留原始符号**
  （负数=上升），档位用中文语义承担可读性——界面翻转符号会让用户拿表格里的数字回填筛选时
  得到相反结果。

条件转换逻辑在 `filters.ts`（纯函数，vitest 直接测）；展示组件 `ScopeBar` / `CategoryPanel` /
`FilterPanel` 全受控，不持状态、不发请求。

### 错误契约（统一,不可绕过）

**所有失败响应只有一种形状**,由 `app/common/error_handlers.py` 的五类处理器统一产出:

```json
{"success": false, "code": "<域>.<原因>", "message": "<面向用户的中文>", "request_id": "0f3a1b2c"}
```

- **业务错误一律「抛」不「返」**。服务层抛 `app/common/errors.py` 的 `APIError` 子类,
  API 层**不做 try/except 转译**——路由层转译会把域内错误码盖成通用码。返值式的失败要靠
  每个调用点自觉检查,漏检即静默,这正是历史上「用户名已存在」被吞掉的机制。
- **`message` 面向用户,可直接展示**。内部细节(异常栈、SQL、路径、OSS object key、配置项名)
  放 `APIError(log_detail=...)`,**只进日志、永不序列化**。
- **状态码按语义**:400 参数不合法 / 401 未认证 / 403 无权限 / 404 不存在 / 409 与状态冲突 /
  413 超上限 / 422 业务不接受 / **503 基础设施不可用**(数据库连接超时、池耗尽,与业务 500 分开,
  以便告警策略分别设定)/ 500 兜底。
- **ProTable 契约 `{data,total,success}` 只表达成功**。列表失败走非 2xx,不再有 `protable_error`
  ——失败混在 200 里,前端无法把「查询失败」和「没有数据」区分开。
- **参数校验错误白名单重建**,只透 `fields:[{field,msg}]`。**绝不透传 Pydantic 的 `input`**:
  它在 `missing` 错误下是整个请求体,曾把明文密码写进响应体与 nginx access log。
- **request id 贯穿**请求头 `X-Request-Id` → 日志 → 错误体,入站已有则沿用。
- **不要给 `FastAPI(...)` 传 `debug=`**,该模式会把 traceback 写进 500 响应体。

两条容易踩的边界:

1. **流式端点(如导出)的失败必须在开流前判定。** 一旦返回 `StreamingResponse`,状态码与响应头
   已发出,之后抛异常只会得到一份**被截断的 200 CSV**,而它会被当成完整数据使用。
2. **全局处理器不拦截 `BackgroundTasks`。** 后台任务必须自捕获,且用 `logger.exception`
   （不是 `logger.error(..., exc)`）——栈是这条路径唯一的线索来源。

前端侧:`src/app.tsx` 的 `errorConfig.errorHandler` 是**错误提示的唯一出口**,页面层不写
try/catch 也能让用户看见失败。确需静默的场景在请求上显式声明 `skipErrorHandler: true`
（如登录页凭证错误由表单自己呈现）——**静默必须是显式声明的结果,不能是忘记处理的副产品**;
声明了跳过,就必须自行呈现。

### Database Design

**Schema**: `analysis`

表结构见上文「V2 数据模型」一节（星型模型：`dim_keyword` / `dim_product` /
`fact_daily` / `fact_weekly` / `site_data_stats` / `sites` / `import_batch`）。
V1 的 `amazon_origin_search_data` 单宽表与 `my_category_stats` 视图已随 V1 退役删除。

**物化视图**：`category_stats` — 按 (站点, 周期) 提供类目下拉选项与计数，导入后由
`import_service._refresh_category_stats` 刷新。

### Performance Optimizations

1. **Connection Pooling**: `database.py` 在模块级建了**两个**引擎，各自持有独立的池，
   服务的负载性质不同，故**分开配置**（`DB_*` 与 `DB_ASYNC_*`）。RDS 的
   `max_connections=400` 是硬顶，全局上限算式是：

   ```
   (DB_POOL_SIZE + DB_MAX_OVERFLOW) × workers        # 同步：导入 COPY、导出游标、迁移
   + (DB_ASYNC_POOL_SIZE + DB_ASYNC_MAX_OVERFLOW) × workers   # 异步：查询与鉴权
   ```

   `start.sh` 固定 4 workers。当前取值 (20+10)×4 + (20+10)×4 = 240，占硬顶六成。
   **RDS 只有 2 vCPU**：PG 同时真正能执行的查询受核心数限制（约 4 个），池开大不提升
   吞吐，只会把「快速可见的失败」换成「整体缓慢劣化」。目标取值是同步 2+3、异步 8+8
   （上限 84），但降池是行为改变，须先观测 `pg_stat_activity` 的 `active` 峰值再落。

   **`THREAD_POOL_LIMIT`（默认 32）与连接池无关，调池时不要跟着一起动。** anyio 的默认
   线程限流器被三类工作共用，只有第一类占连接：同步 `def` 端点（导出）与健康检查探活、
   bcrypt 哈希/校验（每次登录 ~247 ms，不占连接）、同步 `BackgroundTask`（导入，一条线程
   占住整场）。它曾按 `DB_POOL_SIZE + DB_MAX_OVERFLOW` 推导——那样把同步池降到 2+3 会让
   它塌到 5，一个导入在跑就只剩 4 条线程给全部登录。
2. **Query Optimization**:
   - 无筛选的分页总数直接读 `site_data_stats.row_count`（O(1)，不在事实表上聚合）
   - 带筛选的总数用 `FILTERED_COUNT_CAP = 10000` 封顶计数（`analysis_crud.count_filtered`）
   - 每次查询都被约束在「站点 + 视图 + 日期」单一事实切片内，天然限制扫描范围
   - 类目下拉读 `category_stats` 物化视图，不实时聚合
3. **Import**: 走 `COPY` 流式装载到 UNLOGGED 暂存表，内存有界；不再有 pandas 分块或多进程分片
   （V1 的 `BATCH_SIZE`/`MAX_WORKERS`/`FILE_SPLIT_LINES` 等配置已随 V1 一并删除）。
   导入以 `pg_try_advisory_lock` 串行化，同一时刻只跑一个。

## Key Configuration (.env)

完整项与说明见 `.env.example`（新增配置务必同步过去，含单位与安全的本地默认值）。

```bash
# Database (sync + async URLs both required)
DATABASE_URL=postgresql://user:pass@host/db
DATABASE_URL_ASYNC=postgresql+asyncpg://user:pass@host/db
DATABASE_SCHEMA=analysis

# Sites / retention
ENABLED_SITES=US,GB,DE   # 必须是 site_registry 已登记的站点码
DEFAULT_SITE=US
HOT_WINDOW_MONTHS=3

# Upload / OSS（OSS_ENABLED=false 时走本地文件退路，其余 OSS_* 可留空）
MAX_FILE_SIZE=3221225472  # 3GB
OSS_ENABLED=false

# Connection pool（两个引擎各自独立配置，上限是两组相加再 × 4 workers）
DB_POOL_SIZE=20            # 同步：导入 COPY / 导出游标 / 迁移
DB_MAX_OVERFLOW=10
DB_ASYNC_POOL_SIZE=20      # 异步：查询与鉴权
DB_ASYNC_MAX_OVERFLOW=10

# 线程池上限（导出/探活/bcrypt/同步后台任务共用，与连接池无关）
THREAD_POOL_LIMIT=32
```

## Production Environment

| Category | Spec |
|----------|------|
| App Server | Alibaba Cloud ECS · Alibaba Cloud Linux 3.2104 LTS 64-bit · 4 vCPU / 8 GiB RAM |
| Database | Alibaba Cloud RDS PostgreSQL 15.0 · 2C4G · max_connections = 400 |

**Capacity implications for code changes:**
- RDS max 400 connections shared across all app processes; 全局上限是**两个引擎各自的池相加**
  再乘 workers：`(DB_POOL_SIZE + DB_MAX_OVERFLOW + DB_ASYNC_POOL_SIZE + DB_ASYNC_MAX_OVERFLOW) × workers`
  （`start.sh` 固定 4 workers，当前取值算下来是 240）。改任一组前先核对这个算式
- 4 vCPU 上 nginx、4 个 uvicorn worker 与导入任务共享 CPU；导入已由咨询锁串行化，勿再引入并行分片而不实测
- 8 GiB RAM 下导入靠 `COPY` 流式装载保持内存有界；不要引入整文件读取或全量 DataFrame

## Important Constraints

1. **SQLAlchemy 2.0 Only**: Use `DeclarativeBase`, `Mapped`, `mapped_column`, explicit `select()` statements, and `Session.execute(...).scalars()` patterns. Do not introduce `declarative_base()`, `Column(...)` model fields, or `.query()` APIs.
2. **Dual Engines**: Both sync (`engine`) and async (`async_engine`) available - choose appropriately
3. **Schema Qualification**: All models use `schema=settings.DATABASE_SCHEMA`
4. **UPSERT Logic**: CSV imports use raw SQL with `ON CONFLICT (keyword) DO UPDATE` for complex ranking trend JSONB manipulation
5. **Transaction Boundaries**: Service layer controls commits, CRUD layer never commits
6. **Pydantic 2.0 Only**: Use `ConfigDict`, `field_validator`, `model_validate()`, and `model_dump()`. Do not introduce v1-style `class Config`, `@validator`, `@root_validator`, `.dict()`, or `.parse_obj()` usage. Keep ORM models, request schemas, and response schemas typed precisely.

## Development Guardrails

- **Database migrations**: Schema changes must go through Alembic migrations, not ad hoc SQL against application databases. When changing ORM models, add or update the matching migration and verify it targets the configured `analysis` schema correctly.
- **Session/process safety**: Large imports must avoid long transactions and must not reuse parent-process database connections inside worker processes. Worker processes should create and dispose their own database engine/session scope.
- **Async boundaries**: Do not run long CPU-bound CSV parsing, blocking database work, or full-file reads directly on the FastAPI event loop. Use the existing chunked processing, process pool, or background workflow patterns for large imports, and keep request handlers thin.
- **Data-heavy paths**: Design for bounded memory and bounded result sets. Do not load entire GB-scale CSV files into memory, do not return unpaginated table-sized query results, and consider indexes and query plans when adding filters or joins used by search/analysis screens.
- **Tests**: Unit tests should not require a live PostgreSQL instance; database/import tests may require `.env` and should use small fixtures rather than real production CSV files. New tests must be collectable by `pytest` and must not reference deleted or experimental module paths. Bug fixes should include a focused regression test when behavior can be reproduced locally.
- **Configuration and local data**: Keep `.env.example` in sync when adding new settings, including units and safe local defaults. Do not commit `uploads/`, `logs/`, temporary split files, or real customer/search-report CSV data.

## 开发 harness（提交门禁）

**开工先 `openspec list`。** 手上的活儿很可能正是某个在飞变更里没勾完的任务，
先看一眼比做完才发现便宜得多。

### 提交门禁

`git commit` 之前会自动按改动范围跑验证，不通过就拒绝提交。挂在 `PreToolUse` 上
（`.claude/settings.json` → `.claude/hooks/pre_commit.py`），**只卡提交、不卡收尾**——
改到一半停下来问问题是正常节奏，不该被打断；提交则是「我认为这份改动可以了」的声明，
在那里要求它是绿的没有歧义。

| 改动范围 | 跑什么 | 耗时 |
|---|---|---|
| `app/` `scripts/` `test/` `alembic/` 及顶层 `.py` | `ruff check .` + `pytest test/ -q` | ~5s |
| `frontend/` | `pnpm lint` + `pnpm tsc` + `pnpm test` | ~15s |
| 只有文档 / `openspec/` | 放行 | — |

本地 `keywordiq_test` 连得上时会自动带上 `TEST_DATABASE_URL` 解开集成测试，连不上就
只跑单元部分（不因此拒绝提交，CI 有真库兜底）。判据是**真连一次库**而不是探端口——
本机跑着别的 PostgreSQL 时端口一样是通的，据此喂下去会让集成测试整片失败。

每条检查有 300 秒超时，超时按失败处理。卡住的 pytest（抢咨询锁、半开连接）若没有
上限，表现是「agent 不动了」而不是「检查没过」，比失败难诊断得多。门禁自身的回归在
`test/test_pre_commit_hook_unit.py`。

### 铁律检查在哪

分两处，**不重叠**：

- **ruff**（`pyproject.toml` 的 `[tool.ruff.lint]`）——能靠「看一个文件」判定的：
  事件循环上的阻塞调用（`ASYNC` 组）、未用导入、`banned-api` 拦下的 Pydantic v1 /
  SQLAlchemy 1.x 导入。
- **`test/test_conventions.py`**——ruff 表达不了的：配置项与文档的一致性、死配置、
  `except` 块里丢栈的日志。跑 `uv run pytest -m conventions`，亚秒级、不连库。

新增铁律前先问「ruff 能不能查」，能就别手写。手写的规则**必须自带正反 fixture**
（违规样本要被抓到、干净样本不能误报）——检查器自己是没人测的检查器，这正是它烂掉的方式。

### 基线棘轮

规则建立之前就存在的违规登记在 `test/conventions_baseline.toml`，被放行，好让门禁
今天就是绿的。规则同时保证两件事：**新增违规会失败**，且**基线项一旦修好、测试会
要求你把它从基线删掉**（否则基线会腐烂成永久豁免）。

**基线当前是空的**——建立规则时登记的三笔债已全部清偿。空基线是常态，不是终点：
下次有规则拦不住又暂时修不了的历史问题，照样往里登记，只要它继续满足「新增会红、
修好要删」这两条。

已清偿的三笔（留作参照，说明这类问题长什么样）：

- **`user_crud.py` 的返值式错误**——写方法返回 `(success, message, user)` 三元组，
  读方法用 `except Exception: return []` 吞异常。前者要靠每个调用点自觉检查，
  API 层为此按中文子串猜状态码（`"不存在" in message` → 404）；后者更糟，全局处理器
  本会把 `OperationalError` 转成 503，吞掉之后数据库挂了却返回空列表，「查询失败」
  与「没有用户」再也分不开。现已全部改为抛 `APIError` 子类，那 11 处丢栈日志随之消失
  ——它们本就是为了把异常压成元组而存在的。

- **`MAX_FILE_SIZE` 曾是死配置**——`.env.example` 与本文件都公示 3GB 上传上限，
  却没有任何代码读它。现已补上执行点：OSS 路径在 `/oss/import` 触发前 `head_object`
  称量（直传下字节不经应用服务器，那是服务端唯一能称量文件的时机），本地退路在落盘时
  边写边计数、超限即中止并删掉半截文件。
- **连接池的算式曾漏掉一半**——文档写 8/12 且公式是 `(POOL+OVERFLOW) × workers`，
  但 `database.py` 建了同步与异步**两个**引擎共用同一组配置，真实上限要再乘 2。
  三处已对齐到 20/10 并写明算式（当前 240）。是否降到 8/12（上限 160）是另一个决定，
  需先有并发观测数据。

### 项目 skill

- **`local-db`** —— 起测试库、跑含集成测试的全量回归。注意 `225 passed` 与
  `181 passed, 45 skipped` 长得很像，跳过的 45 个恰是唯一碰真实分区表与咨询锁的部分。
- **`verify-import`** —— 用真实体量的报表 CSV 跑一遍端到端导入并逐项断言落库结果。
  改过导入路径就跑一次。
