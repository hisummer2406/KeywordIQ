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
| `fact_daily` / `fact_weekly` | 事实表，按期次日期月度 RANGE 分区 + 按 `site` 二级 LIST 分区。**分区列名两表不同**：日报是 `report_date`，周报是 `report_date_week`——照着日报的列名写周报查询会直接报 `UndefinedColumn` |
| `fact_dropped` | 掉榜词表，按 `anchor_date` 月度 RANGE 分区，**只有一级、没有站点子分区**（站点是普通索引列）。它是差集不是全量，比事实表小一到两个数量级，二级分区的裁剪收益抵不上分区数翻倍的代价 |
| `fact_growth` | 持续增长词表（工具箱），同样按 `anchor_date` 月度 RANGE、无站点子分区。**只有日报**，故没有 `period_type` 列 |
| `site_data_stats` | 按站点/周期/期次的行数（`row_count` 在榜 + `dropped_count` 掉榜），服务分页总数与日期选择器 |
| `import_batch` | 导入批次记录 |
| `keyword_group` / `keyword_group_item` | 词库：用户自建的关键词清单（工具箱 · 我的词库）。普通表，不分区、不参与保留清理、不被导入写入 |

分区表的注册表是 `partitioning.PARTITIONED_TABLES`（表名 → `PartitionScheme(date_column, site_subpartition)`）——「这张表分不分站点」只有这一处答案，`ensure_fact_partitions` 与
`retention_service` 都读它，新增分区表登记进去即自动纳入建分区与按月 `DROP`。

**三条不可违反的约束**：

1. **事实表只追加，不原地更新。** 排名、上期排名、排名变化、是否新词都在写入时算定。
   这是按月归档成立的前提，也是死元组归零的原因。唯一例外是同一期重复导入（先删该
   切片再写）。**`fact_dropped` 同受此约束**：掉榜集合是锚定期的一项事实，锚定期一旦
   确定就不再变化，同样在导入收尾时算定、只追加，查询侧只读不算。重复导入同样是先删
   该期再重算。
2. **写事实表前必须调用 `app.table.analysis.partitioning.ensure_fact_partitions`。**
   分区缺失会让导入直接报错，该函数是幂等的，无条件调用即可。
3. **站点码的唯一事实来源是 `app/site_registry.py`。** 不要在别处硬编码站点列表。
   英国是 `GB` 不是 `UK`。站点的展示元数据（中文名/时区/域名）同样只在那里，界面经
   `GET /api/upload/sites` 取用——**不要为此建库表**。曾有一张 `sites` 参考表存同样三个
   字段，从建表到删除没有任何代码写过它，库里始终是空的；它真正的害处是给站点元数据开了
   第二个事实来源的入口，而两份副本分叉正是 `GB`/`UK` 这类问题的由来。

**迁移注意**：分区表与物化视图不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把
事实表产成普通表并静默丢掉分区。涉及这两类对象的迁移必须手写，并用
`alembic upgrade head --sql` 核对生成的 DDL 里确有 `PARTITION BY`。

**新增模型必须登记进 `migration_scope.py` 的 `V2_TABLES`**（`alembic/env.py` 的
`include_object` 用它做白名单；清单单独成模块是为了让测试能 import 到同一份——`env.py`
在 alembic 运行时之外导不进来）。漏登的表 autogenerate 既看不见也不报错，对模型改动
一律回答「没有差异」。`test_conventions.py` 会拦住漏登。

**列注释参与 autogenerate 比对**，因此模型里的注释文本要与库里一字不差；改注释就要配
一条迁移。跨表复用的文本（`site`、`period_type`）收在
`app/table/analysis/column_comments.py`。这不是洁癖：曾有约 40 条纯注释差异常驻在
autogenerate 输出里，把 `user_center` 时间戳丢了时区这条真差异整个盖住。集成测试
`test_v2_integration.py::test_models_match_migrated_schema` 现在要求 autogenerate 对
迁移到 head 的库产出**空**结果。

**V2 查询层（site-scoped-query + window-scoped-rows，已落地）**：分析查询查 V2 星型模型，
作用域是「**单一站点 + 单一视图（DAY/WEEK）+ 锚定期 + 行窗口**」——站点与视图那两维仍是
硬边界，MUST NOT 突破；时间那一维从「单一事实切片」放宽为一个窗口：

| | 天视图 | 周视图 |
|---|---|---|
| 行窗口（决定哪些词进表） | 锚定日往前 7 天 `[d-6, d]` | 锚定周往前 4 周 `[w-3, w]` |
| 历史参照点（只补齐已选定的行） | `d-1` / `d-3` / `d-7` | `w-1` / `w-4` / `w-12` |
| 趋势窗口（小图点数） | 近 7 天 = 7 点 | 近 4 周 = 4 点 |

**行集合 = 在榜 ∪ 掉榜**：在榜 = 锚定期有数据；掉榜 = 行窗口内出现过但锚定期没有（读
`fact_dropped`，查询侧只读不算）。开关是请求字段 `include`（`ALL`/`LISTED`/`DROPPED`，
默认 `ALL`），它是行集合维度**不是筛选条件**。掉榜行恒排在在榜行之后、不随升降序翻转。

**但带筛选的路径仍然锁死在单一事实切片里。** 筛选一律以锚定期那一行为准,必然后果是
**任何筛选一生效、掉榜行全部消失**（它们在锚定期没有行可匹配），此时 `dropped_total`
短路为 0、掉榜段连查都不查。所以「筛选后的扫描范围由一期数据决定」这个性能论据没有变——
放宽的只是无筛选路径的行集合。界面必须明示这一点，否则用户会当成系统弄丢了数据。

要点：

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
- **两个视图都附带只读的走势与各点日期**（天 7 点 / 周 4 点，`trends_stmt` +
  `shape_trends`，「先分页再批量补附加」），以及三个历史参照点的排名。**走势与历史排名
  共用同一次回溯查询**：回溯范围取「趋势窗口」与「最远参照点」的大者
  （`fetch_lookback_days`，天 7 天 / 周 84 天），历史排名因此不多发一条查询。
  代价是回溯范围**比趋势窗口宽**，多出来的期次只喂历史排名列、不进小图，所以
  `shape_trends` / `shape_trend_dates` 必须拿到 `on`/`view` 做裁剪——漏传就会画出
  天 8 点 / 周 13 点，而这种错误没有任何地方会报错（导出路径踩过一次）。
  **排序白名单**按视图，走势与历史排名字段一律不许排序。
  **日视图不再附带「当周排名变化」**——它把周报数据混进日报视图，让「这一行属于哪份数据」
  变得含糊，还要求查询侧自行定义周边界（`date_trunc('week')`），而导入侧只存文件名日期、
  不定义周边界。要看周维度的变化就切到周视图。
- 列表响应走 ProTable 契约 `{data,total,success}` + 作用域回显。集成测试
  `test/test_site_scoped_query_integration.py`（`TEST_DATABASE_URL` gate）。
- 演示数据：`scripts/seed_demo_data.py`（连续 7 天 + 对齐周报，让走势可见，仅本地）。

**工具箱 · 持续增长市场（`/api/toolbox/growth/*`，权限点 `toolbox:view`）**：近 7 天名次
持续前进的关键词榜。命中口径三条同时成立——窗口内每期都在榜、累计净增 `rank(d-6) − rank(d)`
> `GROWTH_MIN_RANK_GAIN`、6 个日环比全部 ≥ `GROWTH_MIN_DAILY_RATE`。五条要点：

- **只有日报**。周报没有「连续 7 天」，`period='WEEK'` 的导入不算定、不写表。
- **导入收尾算定（`importer.load_growth`），查询侧只读**，与 `fact_dropped` 同一模式。
  口径只有那一处定义；查询层或脚本里再写一遍，漂移的表现是「界面显示的行不满足界面
  自己写的口径」，没有任何地方会报错。
- **改 `GROWTH_*` 阈值不会影响已算定的期次**（表里只存命中行，不存全量指标）。改完
  必须跑 `scripts/backfill_growth.py` 重算，**上线时也必须跑一次**，否则库里明明有三个月
  日报、界面却要等 7 天才出现第一条榜单。
- **窗口内缺期时榜单据实为空**，缺失期次随响应回传、界面必须明示——「今天没有增长词」
  与「有两天数据没导进来」在界面上长得一模一样。
- **这一屏首屏不自动查榜单**（与分析查询屏相反，那一屏进去就查）。榜单按锚定日切片，而
  默认的最新一期恰恰最可能因窗口缺期或口径过严而为空，先查一次既浪费请求、又让用户在
  还没做任何选择时就看到空表。

  代价是空表格从两种成因变成**三种**——还没查 / 窗口缺期 / 口径下确实没命中——含义
  完全不同却长得一样。所以「还没查」必须在三处同时说清：**不显示条数**（「持续增长词：0」
  会被读成今天没有增长词）、**不显示「条件已修改」标签**（用户一个条件都没改，那句话是
  错的）、**空状态提示去点「立即查询」**。少任何一处，缺陷就伪装成一条正常的空榜单。

**工具箱 · 我的词库（`/api/toolbox/library/*`，权限点同为 `toolbox:view`）**：用户自建的
关键词清单，把关心的词攒起来长期盯排名。两张普通表 `keyword_group`（分组）/
`keyword_group_item`（条目），不分区、不参与保留清理、不被导入路径写入。六条要点：

- **行集合由条目决定，事实表只补数**——与分析查询屏相反（那边行集合 = 在榜 ∪ 掉榜，
  由事实表决定）。盯盘查询由条目表驱动、LEFT JOIN 事实表与 `fact_dropped`，每个词恒有
  一行，状态三选一：**在榜 / 掉榜 / 该期无数据**。盯的词掉出榜单恰恰是最该看见的事，
  不能因为事实表里没有行就让它消失。
- **条目只存 `keyword_id`**，不另存关键词文本与站点码（`dim_keyword` 已唯一确定
  `(site, keyword)`）。**分组绑定站点且建后不可改**；同一批词要盯三个站点就建三个组。
- **两个上限都是拒绝式的**：`LIBRARY_MAX_GROUPS_PER_SITE`（20）、
  `LIBRARY_MAX_ITEMS_PER_GROUP`（500），超限 422 且**整批不写**。后者同时是盯盘查询的
  规模保证——那条查询最坏是上限这么多行的嵌套循环（两侧走
  `idx_fact_*_keyword_history` 与 `fact_dropped` 主键），**上调前必须重测**。
- **入库入口只有分析查询屏**（勾选结果行 → 加入词库），词库屏只能移除。理由是身份：
  从结果行加入时 `keyword_id` 是现成的，词必然真实存在于该站点；手工录入要按文本反查，
  错别字、多余空格、跨站点同名词各自的失败行为在盯盘清单里没有正确答案。
- **私有性由每条查询里的 `user_id` 保证**，不靠界面不展示；别人的分组一律 404 而非 403
  （403 等于确认了 id 存在，而 id 连续自增）。**不新增权限点**——「禁止某人管理自己的
  收藏夹」不是真实的授权需求。
- **这一屏进去就查**（与持续增长市场屏相反）：盯盘的空表都与「还没做选择」无关。
  但三种成因要分开说——**没有分组 / 站点没数据 / 组里没词**，三句文案各不相同。
- **分组管理是表格上方独立的一块（`GroupBar`），不做侧栏**：盯盘表格要 1688px，侧栏形态下
  右侧只剩 1292px（1920 视口实测），近 400px 被裁进横向滚动。分组一览有价值，但代价必须
  落在**纵向**——纵向可以滚，横向被裁掉的列找不回来。选中/改名/删除/新建都在这一块里。

事实行的列投影由 `analysis_crud.fact_metric_columns` 提供，**两屏共用一份**；复制第二份
不会报错，表现是同一个词在两屏显示不同的值。

「外观专利」「全球商标库」只是占位屏：仓库里没有任何专利/商标数据，**不发请求、无后端
端点**。四个二级菜单共用 `toolbox:view` 一个权限点——给没有端点的屏各开一个权限点，就是
再造一遍 `user:manage` 那种「勾了也不生效」的复选框。

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
│   ├── toolbox/       # 工具箱查询层（持续增长市场 growth_crud/service/api/schemas）
│   │                  # 模型 fact_growth 寄居在 analysis/fact_model.py——分区、保留、
│   │                  # 导入收尾都在那个域内，登记一处即被三处接管
│   └── upload/        # OSS 直传凭证 + 三段式导入（oss_client / import_source / importer / import_service）
│                      # + 批次历史与重导（import_batch_crud / import_history_service）
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
   **同时落一条 `UPLOADING` 批次记录，且落库先于交出凭证**——直传那一段服务端全程不知情，
   没有这条记录，用户关掉标签页就等于什么都没发生过（他以为传上去了，历史屏查不到）。
   顺序反过来则是「能用的凭证 + 不存在的记录」，正是要消灭的那个状态；落库失败就不签发。
2. **触发导入** `POST /oss/import`：前端回传 object key；后端校验 key 落在授权前缀内、对象存在，
   **按 object key 认领第 1 步那条 `UPLOADING` 批次**（认领不到就新建——回收可能刚把它判失败，
   或调用方跳过签发直接触发），再交后台任务 `run_import`。
3. **三段式导入**（`importer.py`，源经 `ImportSource` 抽象）：从 OSS 对象**流式** `COPY` → UNLOGGED
   暂存表 → 补齐维度 → 关联上一期算派生值、**追加**写事实分区（写前必调 `ensure_fact_partitions`）。
   内存有界，不整文件落地。

   **维度补齐只在属性真变时才写。** `dim_product` 的 UPSERT 带
   `WHERE dim_product.title IS DISTINCT FROM EXCLUDED.title`，这条 WHERE **不是冗余**：
   PostgreSQL 的 UPDATE 无论新旧值是否相同都写新元组、标死旧元组、并在该行涉及的每个索引
   上插新条目——而 title 上挂着这张表最大的索引（GIN trgm，1.3 GB）。无条件更新意味着每次
   导入重写**所有已存在的商品行**（一份日报约 40 万行 × 3 个槽位 ≈ 120 万个 ASIN 提及，
   绝大多数早已在库里），而且会自我加速恶化：维度表越大，"已存在"的比例越高，白做的更新
   越多，承受它们的索引也越大。实测同一份 10 万行日报因此从 54 秒退化到 416 秒（issue #33）。
   用 `IS DISTINCT FROM` 而非 `<>` 是因为后者遇 NULL 结果为 NULL，将来放宽可空性会静默
   退化成"NULL 的行永远不更新"。`dim_keyword` / `dim_brand` / `dim_category` 走
   `DO NOTHING` + `NOT EXISTS` 预筛，本来就不重写既有行。
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

**`QUEUED` 必须真的会被接手**，否则它就是个死标签。接手者是 `app/table/upload/import_queue.py`
的轮询任务（lifespan 里起，间隔 `IMPORT_QUEUE_POLL_SECONDS`，默认 30 秒；多 worker 各起一个，
靠同一把咨询锁保证仍是串行）。历史上没有它：日报 300K 行要跑约 4 分钟，这期间提交的第二个文件
停在 `QUEUED` 永不开始，界面显示「队列中」，没有任何东西会回头看它——用户在等一件不会发生的事。

因此两条约束：

- **接手用轮询，不用「导入结束时接着跑下一个」**。链式接力在进程被杀、导入崩在半路、或前一个
  导入由别的 worker 执行时都会断链，而断链的表现恰恰就是「永远排队」，与它要修的病一模一样。
- **重建不出数据源的批次不许留在 `QUEUED`**。本地退路（`OSS_ENABLED=false`）没有 object_key，
  批次里只有文件名，接手时无从重建源——这类当场判 `FAILED` 并写明「请稍后重新上传」。
  留在 `QUEUED` 等于撒谎。

**`UPLOADING` 适用同一条纪律**（`import_service.reap_stale_uploads`，挂在同一个轮询任务上）。
回收的判据不是「多久算久」，而是**这次上传还有没有可能传完**：直传每个请求都由 STS 凭证签名，
凭证一过期后续分片一律被拒。阈值因此由 `OSS_STS_DURATION_SECONDS` 推导（`+300` 秒宽限，
`config.upload_stale_seconds`），**不是独立配置项**——两个旋钮迟早矛盾，而短于凭证有效期会把
正在好好传的 1.5GB 周报判死。超时后按对象是否真在 OSS 分流：

- **在** → 字节传完了、只是没触发导入（关标签页的典型形态）：置 `QUEUED` 交给上面那个接手者，
  **不让用户重传**。这条路径**必须重新执行大小上限**——上限本来在 `/oss/import` 里执行，
  而这里绕过了它，不补就等于开了一条「不调 /oss/import 即可绕过上限」的路。
- **不在** → 判 `FAILED`，写明请重新上传。

**导入批次有两个读取出口，职责不同、不要合并**：上传屏的「最近导入状态」看板
（`GET /processing-status`，固定最近 5 条 + 前端 5 秒轮询）回答「我刚传的文件跑到哪了」；
导入历史屏（`GET /batches`，ProTable 契约 + 站点/周期/状态三项筛选 + 分页，`per_page` 上限
100 且**超限拒绝不截断**）回答「上周 DE 的周报到底导没导」。历史屏**不轮询**——一张带筛选
的表每 5 秒整体刷新，用户翻到第 3 页会被刷回第 1 页。两处查询共用 `import_batch_crud`
的同一份组装代码。

**重导**（`POST /batches/{id}/retry`，权限点同为 `upload:import`）凭批次留存的 object key
重新导入，不必重传 GB 级文件。两条约束：

- **只允许 `FAILED`。** 重导会先删该 (站点, 周期, 期次) 的现有切片再重写；对 `COMPLETED`
  做这件事等于让一份正在被查询的数据先消失、再花几分钟重建。也正因只允许 FAILED（失败
  已整体回滚、切片本就是空的），界面不做二次确认——若将来放开 COMPLETED，必须同时加回确认。
- **重导新建批次记录，不改写原记录。** 原记录的状态与错误信息是排查依据，抹掉它历史上
  就只剩一条看不出曾经失败过的成功记录。重导没有独立执行路径，就是拿同一份归属再调一次
  `run_import`，因此同样受咨询锁串行化、拿不到锁进 `QUEUED` 由轮询任务接手。

准入判定（不存在 404 / 非 FAILED 409 / `object_key` 空 422 / 前缀越权 400 / 对象已删或超限）
**必须全部在触发后台任务之前完成**——全局处理器不拦 `BackgroundTasks`，校验放进后台任务
用户只会看到「已触发」然后什么也不发生。越权前缀与对象称量两道校验与 `/oss/import` 共用
`oss_client.verify_source_object`：重导若少掉越权那道，它就成了绕过授权前缀触发任意切片
导入的入口。

> V1 的 `csv_processor.py`（pandas 分块 + `ON CONFLICT (keyword)` UPSERT + `ranking_trend_day` JSONB
> 7 天走势）随 V1 `/upload-csv` 端点一同退役，不要在 V2 路径复用。

### Authentication & Authorization

- **自建 JWT + RBAC**（`fastapi-user-auth`/`fastapi-amis-admin` 已随 amis 于阶段七移除）。
  用户模型 `UserCenter`（`user_center` 表）、`passlib` bcrypt 密码、`AuthService`（PyJWT/HS256/
  `ADMIN_SECRET_KEY`）、`simple_auth`（登录、`get_current_user`、`require_permission`、
  `require_super`）。
- **RBAC**：`role`/`permission`/`role_permission`（M2M），`user_center.role_id`（每用户一角色）。
  权限点只有四个：`analysis:view`/`analysis:export`/`upload:import`/`toolbox:view`；内置角色
  admin/operator/analyst（见 `app/auth/permissions.py`）。逐路由 `require_permission(code)` 授权，
  is_super 兜底。前端在 `access.ts` 按权限码显隐菜单。
- **用户管理是「不可授予」的能力，不走权限点**：`/api/user/*` 五个端点一律
  `Depends(simple_auth.require_super())`，判据只看 `is_super`，**不看角色码**。前端入口在
  右上角头像下拉里（`components/RightContent`），顶栏不出现（`/user` 路由 `hideInMenu`，
  但 access 守卫照常生效——菜单隐藏不是访问控制）。

  两条历史教训写在这里，别再走回去：**（1）** 曾有 `user:manage` 权限点，收紧后它成了
  「勾了也不生效」的复选框——管理员以为把它授予了运营，运营却仍然 403；可授予但无效的
  权限点比没有更糟，故连同从未被任何代码读过的 `role:manage` 一并从矩阵中删除
  （迁移 `d0e1f2a3b4c5`）。**（2）** 曾有一个 `get_super_user`，判据是「is_super **或**
  role_code == 'admin'」——那等于让「谁能管用户」变成改角色配置就能改变的事，已被
  `require_super` 取代（它声明后从未被使用）。回归在 `test/test_rbac_auth_unit.py`：
  admin 角色 + 全部权限点、只要 `is_super` 为假就必须 403。

  代价是**能管用户的唯一判据就是 `is_super`**：若唯一超管的该位被误改为 false，界面上
  没有自救入口，只能靠 `scripts/seed_admin.py` 或直接改库恢复。
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

- **首屏按默认作用域自动查一次**。进屏先拉站点、作用域与类目（选择器的前提），拿到结果
  立即用默认站点 + 按天 + 最新一期 + **默认筛选（只看新词）**查一次。这一屏是主力数据屏，
  用户进来就是要看数据的，而他最常问的第一个问题是「今天有哪些新词」。**延迟到点击才查的
  是持续增长市场屏，不是这一屏**——两屏曾被做反过一次，改这段之前先确认改的是哪一屏。

  **默认值只有一处定义**：`filters.ts` 的 `defaultFilters()`，初始草稿、首屏自动查询、
  「重置条件」三处共用。它带 `isNew: true`——这个函数从前叫 `emptyFilters`，改名是因为
  名不副实的工厂会被下一个人当空值直接用（测试里正在这么用）。要真正的中性值请显式写
  `{ ...defaultFilters(), isNew: undefined }`。

  **条件初始化有三级优先级**（`savedMode.ts`），高者胜出：**会话快照 > 用户保存的条件 >
  内置默认**。三者的存活期完全不同，这决定了它们各自存哪：

  | | 存活期 | 存在哪 | 谁清它 |
  |---|---|---|---|
  | 会话快照 | 一次页面会话，刷新即失效 | 模块级内存 | `doLogout` / 刷新 |
  | 保存的条件 | 跨登录，直到用户点「重置条件」 | `localStorage`，键带 userId | 只有「重置条件」 |
  | 内置默认 | 永久 | 代码 | —— |

  保存的条件（「保存当前模式」，对标卖家精灵）存**站点 + 视图 + 筛选 + 排序**，
  **不存日期**（永远取最新一期——存绝对日期的话，保留窗口滚过后那个月的分区已被 `DROP`，
  条件查不出任何东西）、**不存检索词**（一次性查找不是「常看的模式」）。四条要点：

  - **它必须活过 `doLogout`，这不是漏清。** 会话快照是**无主的单份**，换账号会串，只能清；
    保存的条件按 `userId` 分键，乙读的是乙的键，天然不串。清了它「下次登录还是这组」就
    不成立——那正是这个功能的全部意义。取不到 userId 时**不读不写**，绝不回退到共享键。
  - **payload 带 `SAVED_MODE_VERSION`，改形状就要 +1**，版本不符**静默丢弃且不做迁移**。
    这条比 `CACHE_VERSION` 更要紧：会话快照最多活一次刷新，这份能跨月、跨多次上线。
  - **存的是生效条件不是草稿**。实现上靠「`dirty` 时拒绝保存」保证——`draftSig` 由
    `toSearchBody(scope, filters)` 得出，作用域与筛选都在里面，`dirty` 为假即「草稿 === 生效」。
    这样不必新增状态，也不必动会话快照的形状。
  - **保存的站点下线时乐观回退**：首屏直接拿它发 `/scope`（后端对非法站点报错不回退），
    失败则回退默认站点、清掉保存项、**自行提示**。那次调用带 `skipErrorHandler`，
    不自己说就成了静默降级——用户以为在看自己保存的站点，实际在看另一个。

  **首屏按保存的作用域初始化时，`reloadScopeMeta` 必须带 `keepCategories`**。它平时会清空
  已勾选的类目（作用域变了，旧站点的类目在新站点下可能不存在），但首屏这次不是「变化」
  ——作用域从一开始就是保存的那个、类目也是一起存的，照常清掉等于让「保存类目」静默失效。

  首屏这次查询**带筛选**，两个后果都是既有语义、不是本屏特例：总数走 `count_filtered`
  精确实算而非 `site_data_stats` 的 O(1) 预存值；**掉榜行不在结果内**。

  自动查询**只发生在无会话缓存的首次进入**（`if (restored) return;` 那道闸之后），切走
  再切回仍然一个请求都不发。条件必须用 `reloadScopeMeta` 返回的 `s` 组装，**不能读
  `scope` 状态**：它刚被 `setScope` 写过而 setState 是异步的，读到的还是空日期，组装出的
  条件会被 `request` 开头 `!body?.date` 那道守卫静默挡掉——表现与"没做自动查询"一模一样。

  站点一期数据都没有时不发请求，此时空表格**必须说明是没有数据**（`scopeHasNoDates`），
  不能显示「搜索结果：0」——那会被读成条件把行筛没了，用户会去排查筛选条件。
- **条件改动显式触发查询**。条件改动只进草稿态，点「立即查询」才生效（`draft` 与 `applied`
  是**两份**状态，不是一份加脏标记——否则「表格现在显示的是哪组条件」无从读出，导出会
  跟着漂移）。除首屏外两个例外：排序/翻页走 ProTable 自身 `request` 即时生效；改站点或
  视图立即重拉 `/scope` 与 `/categories`（小表）但**不**重查表格，否则用户会在属于上一个
  站点的日期清单里做选择。
- **一个筛选维度只有一个入口**。关键词与 ASIN 归顶部检索框（按 `^B0[A-Z0-9]{8}$` 判定走哪个
  字段，判定结果必须回显），过滤区不再重复提供。**「重置条件」清成默认（含只看新词）而非
  清空**，因此界面上没有一键回到「全部词」的入口——加一个「恢复默认」按钮会与「重置」
  语义相近，用户得先分辨差别才能选对，正是这条约定要避免的形态。想看全量榜单就把新词
  开关拨到「全部」，多一次点击是自觉代价。**「重置条件」还连带删除用户保存的默认条件**
  （否则重置完、下次登录又回到那组他刚明确不要的条件），**不做二次确认**——需求方的明确
  选择，已知代价是删除不可撤销而这个按钮看起来只是改改表单。它只改筛选，不动站点/视图/排序。
- **份额是 0~100 的百分数**，界面、快捷档位、接口一路不做换算；**排名变化保留原始符号**
  （负数=上升），档位用中文语义承担可读性——界面翻转符号会让用户拿表格里的数字回填筛选时
  得到相反结果。

- **结果表格可勾选行并批量「加入词库」**（唯一的入库入口，见上文词库一节）。可选分组只列
  **生效作用域**站点下的分组——草稿切了站点但没查时表格里还是旧站点的行，跟着草稿走会把
  US 的词加进 DE 的分组。掉榜行同样可勾选（它有 `keyword_id`，「掉出去了想继续盯」正是
  典型动机）。**选中状态不进会话快照**：它是一次性界面状态，切走再切回清空是预期行为，
  因此这一项不需要动 `CACHE_VERSION`。

条件转换逻辑在 `filters.ts`（纯函数，vitest 直接测）；展示组件 `ScopeBar` / `CategoryPanel` /
`FilterPanel` 全受控，不持状态、不发请求。

**切走再切回不刷新，这是约定不是缺陷。** umi 4.6 没有 keepalive，路由一变这个组件真的被
卸载，切回来是全新实例——条件全丢 + 4 个接口重发（`sites` / `scope` / `categories` / `search`）。
现在由 `stateCache.ts` 的会话内快照承载状态：`useCachedState` write-through 写入，挂载时有
快照就原样复原并**跳过整段初始化**，一个请求都不发。要新数据仍然只有「立即查询」。四条要点：

- **短路只作用于复原后的第一次请求**（一次性）。写成「参数相同就用缓存」会把同参数的
  「立即查询」也吃掉，而那是用户强制刷新的唯一入口——数据刚导完想看新的，点了却什么都不动。
  翻页、改排序一律真的去查。
- **缓存在 `doLogout` 里清**（`app.tsx`，与清令牌 / 清 initialState 并列）。不清的话同一
  标签页内换账号登录会看到上一个用户的条件与结果行。
- **快照带 `CACHE_VERSION`，改状态形状要 +1**。否则开发期热更新后残留的旧快照会以
  `undefined` 渗进 UI，表现为零散的空白控件，极难归因。
- **缓存是模块级的，测试之间会串**：`index.test.tsx` 的 `beforeEach` 必须 `clearSnapshot()`，
  漏掉的表现是第二个用例起全部看到一屏空数据，而报错（找不到某个按钮）指不到这个原因。

滚动位置一并恢复，且**必须等结果行落到 DOM 之后**——行没渲染时页面高度不足，`scrollTo`
会被浏览器夹到当前最大滚动量（实测就是回到顶部）。记录侧用 scroll 监听器，**不要改成
在 cleanup 里读一次**：导航时新页面较矮会把滚动量夹到 0，那个夹取与卸载谁先谁后没有保证，
排在前面就把正确值覆盖成 0，功能静默失效。

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
`fact_daily` / `fact_weekly` / `site_data_stats` / `import_batch`）。
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
   - 无筛选的分页总数直接读 `site_data_stats` 的 `row_count + dropped_count`（O(1)，
     不在事实表上聚合）。这两个预存值同时也是**分段分页的分界线**，与事实/掉榜写入在同一
     事务内提交——不一致会让跨界页重复或漏行，而那种错误在界面上表现为「某一行出现两次」，
     极难归因
   - 带筛选的总数**精确实算、不封顶**（`analysis_crud.count_filtered`）。曾有 V1 沿用的
     `FILTERED_COUNT_CAP = 10000`，在 V2 是错的：带筛选的查询本就锁死在单一切片里，计数代价
     由切片大小决定、不随窗口增长（实测热缓存日报 168ms、周报 242–375ms），而封顶换来的
     是一个无法与精确值区分的 total——前端据此算页数，超出上限的命中行在界面上够不着
   - 每次查询都被约束在**单一站点、单一视图**内；时间维上，带筛选走单一事实切片，无筛选
     走行窗口（天 7 期 / 周 4 期）。掉榜段读的是导入时算定的 `fact_dropped`，查询侧不做
     反连接——那正是决策 1 要避开的「每翻一页重算一次」
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

# 持续增长市场的判定口径（仅日报）。改了要跑 scripts/backfill_growth.py 重算历史期次
GROWTH_WINDOW_DAYS=7          # 窗口期数（含锚定日）；7 期产生 6 个日环比
GROWTH_MIN_RANK_GAIN=10000    # 累计净增名次下限（不含）
GROWTH_MIN_DAILY_RATE=0.10    # 每个日环比的下限（含）

# 词库上限（超限 422 拒绝、整批不写）。每组条目数同时是盯盘查询的规模保证，上调前先重测
LIBRARY_MAX_GROUPS_PER_SITE=20
LIBRARY_MAX_ITEMS_PER_GROUP=500

# Upload / OSS（OSS_ENABLED=false 时走本地文件退路，其余 OSS_* 可留空）
MAX_FILE_SIZE=3221225472  # 3GB
OSS_ENABLED=false
IMPORT_QUEUE_POLL_SECONDS=30  # 排队批次多久接手一轮；0 = 不自动接手

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

## 并行会话：一个变更一个 worktree

**这个仓库经常同时有多个会话在干活。** 默认假设不是「只有我一个人」。
开工流程见 skill **`start-change`**（认领 → 建 worktree → 收工），下面是它背后的道理。

### 一个变更一个 worktree，路径在仓库外

每个 openspec 变更配一个分支 `change/<变更id>` 和一个 worktree，路径
`~/.claude-worktrees/<变更id>`——**不要放 `.claude/worktrees/`**。放仓库内，每个
watcher（uvicorn `--reload`、webpack、pytest 收集）都得单独配排除，漏一个就出事：
曾有 worktree 让 WatchFiles 把 70 多个文件判为源码变更，连触发 9 次全量重载后
reloader 直接退出（退出码 144，日志是优雅关闭，极易误判成应用问题）。放仓库外，
所有 watcher 都看不见它。

worktree 解决的是**共享索引与共享工作树**这两类碰撞：

- `git commit` 提交的是整个索引，别的会话 staged 的文件会被一并带走（真实发生过）
- 文件在 Read 与 Edit 之间被另一个会话改写，编辑直接失败

### worktree 解决不了的三类，要另外守

1. **重复劳动。** 两个 worktree 不阻止两人做同一件事，只会把撞车从「改到一半」
   推迟到「合并时」。`openspec list` 只说明变更**存在**，不说明**有人在写代码**——
   判据是分支：`git branch --list 'change/*'`。开工前查，开工后立刻建分支占位。
2. **共享测试库。** `keywordiq_test` 是全局的一份。**drop 前必须先查活动连接**
   （`pg_stat_activity`），无条件 `dropdb` 会把别人正在跑的集成测试拦腰打断，
   而对方看到的是一堆无从解释的报错。

   **反过来也成立：跑一次全量 pytest 会清空这个库里的业务数据。**
   `test_import_service_integration.py` 与 `test_oss_ingestion_integration.py` 的夹具在
   每个测试后 `TRUNCATE analysis.fact_daily, fact_weekly, dim_keyword, dim_product,
   site_data_stats, import_batch RESTART IDENTITY CASCADE`——它们测的是 `run_import`，
   而后者内部提交，回滚兜不住，只能靠 TRUNCATE 收尾。所以**别往 `keywordiq_test` 里放
   你还要用的数据**：`verify-import` 灌进去的真实报表，下一次 `git commit`（门禁会跑
   全量）就没了，且没有任何提示。要留着就换个库名。

   同理，集成测试里的计数**必须带作用域**（`WHERE site=... AND report_date=...`）。
   裸 `count(*) FROM analysis.fact_daily` 只在"库里除了我什么都没有"时成立，一旦有别
   的会话留下的已提交数据就会失败，而报出来的 diff 完全看不出是这个原因。
3. **共享端口。** 只有主仓跑 dev server（8000/8001）。worktree 里要跑就从变更 id
   派生端口，不要抢。dev server 被别人杀掉或抢走时，先确认是不是并行会话所致。

### 提交前看索引列

`git status --short` 的**第一列是索引**。看到不属于本次改动的条目，用
`git commit -- <路径>` 按路径提交，不要裸 `git commit`——后者提交整个索引。
这条与 worktree 无关，是操作纪律，全面 worktree 化之后同样要守。

**门禁会拦一半。** 索引与工作树对同一文件不一致时（暂存后又改了、或暂存后从磁盘
删了），裸 `git commit` 被直接拒绝——因为门禁的 ruff / pytest 读的是磁盘，而裸提交
提交的是索引，两者不一致时那份"跑绿了"的验证描述的不是这次提交的内容。历史上一个
含未用 import 的文件正是这样进的主干：它被暂存后从磁盘删掉（索引状态 `AD`），
ruff 根本看不见它。`git commit -a` 与 `git commit -- <路径>` 提交的都是工作树内容，
与被 lint 的那份一致，故不受此限。

门禁**拦不住**的那一半：索引里那些与你无关、内容又和磁盘一致的文件（别的会话暂存
好的）——它们看起来完全正常，只有你知道它们不属于这次提交。所以索引列还是得自己看。

> 门禁按**提交实际落地的仓库**取暂存区（`target_repo` 解析 `cd X && git commit`
> 与 `git -C X commit`），不是按会话 cwd。此前正是这里漏过一个未用 import：会话在
> worktree 里往主仓提交，门禁查 worktree 的空暂存区，查了个空就放行。

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

- **`start-change`** —— 开始或接手一个变更时先跑它：认领（防止与并行会话重复劳动）、
  建仓库外的 worktree、收工时按路径提交并清理。**动手写代码之前用**，见上文
  「并行会话」一节。
- **`local-db`** —— 起测试库、跑含集成测试的全量回归。注意 `225 passed` 与
  `181 passed, 45 skipped` 长得很像，跳过的 45 个恰是唯一碰真实分区表与咨询锁的部分。
  **drop 测试库前先查 `pg_stat_activity`**，别人可能正在用同一个库。
- **`verify-import`** —— 用真实体量的报表 CSV 跑一遍端到端导入并逐项断言落库结果。
  改过导入路径就跑一次。
