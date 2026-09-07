# CLAUDE.md

本文件是这个仓库的开发约定与设计记录，**面向所有在这里干活的智能体**，不只是 Claude Code
——名字只是历史沿革。仓库根部的 `AGENTS.md` 是给 Codex 等其他智能体的入口，它**不复制**
本文件的内容，只列出本文件里少数几句「只对 Claude Code 这个运行环境成立」的差异
（提交门禁、skill 机制、`AskUserQuestion`）。**新增约定写进本文件，不要写进 `AGENTS.md`。**

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
                                       # 生产常态由 systemd 托管（etc/keywordiq.service），
                                       # start.sh 是 unit 的 ExecStart；手动跑它只是开发/应急
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
| `fact_growth` | 持续增长词表（关键词工具），同样按 `anchor_date` 月度 RANGE、无站点子分区。**只有日报**，故没有 `period_type` 列 |
| `site_data_stats` | 按站点/周期/期次的行数（`row_count` 在榜 + `dropped_count` 掉榜），服务分页总数与日期选择器 |
| `import_batch` | 导入批次记录 |
| `keyword_group` / `keyword_group_item` | 词库：用户自建的关键词清单（关键词工具 · 我的词库）。普通表，不分区、不参与保留清理、不被导入写入 |

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

**关键词工具 · 持续增长市场（`/api/keyword/growth/*`，权限点 `keyword:view`）**：近 7 天名次
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

**关键词工具 · 我的词库（`/api/keyword/library/*`，权限点同为 `keyword:view`）**：用户自建的
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

**关键词工具 · 排名趋势详情（`GET /api/keyword/trend`，issue #86）**：单关键词的日/周排名
历史，市场分析与我的词库共用同一个只读端点，抽屉形式，不设导航菜单。查询固定
`keyword_id + site + view`，按 `idx_fact_{daily,weekly}_keyword_history` 与
`fact_dropped` 主键取数，不扫全站榜单。五条要点：

- **五态分类，且不信任 `site_data_stats` 在保留下界之前的行。** `retention_service`
  只 `DROP` 事实表/`fact_dropped`/`fact_growth` 的月度分区，**从不清理 `site_data_stats`
  的对应行**——这是既有代码一个尚未触发问题的缺口，本变更不修。判定顺序因此把
  `report_date < retention_cutoff` 放在最前面、且**不查** `site_data_stats`：否则一个
  分区早被 `DROP`、统计行却仍残留的月份会被误判成「已导入但未收录」，让用户以为那份
  数据还在，只是没收这个词。其余四态：在榜（`RANKED`）/ 掉榜（沿用既有 `fact_dropped`
  口径，`DROPPED`）/ 报表已导入但未收录（`NO_DATA`）/ 报表未导入（`NOT_IMPORTED`）。
- **周视图的期次步长是 7 天，不是自然周边界。** 复用 `app/table/upload/importer.py`
  的 `_PERIOD_CONFIG["WEEK"]`「上一期偏移」——`previous_ranking`/`ranking_change` 已经
  在用这个数，这里不新造一套周定义。
- **锚定期解析统一是「取 `<= on` 的最新一期，取不到就退化为全局最新一期」**
  （`trend_service._resolve_anchor`）。这一条规则同时覆盖两种情形：同视图的正常查询
  （`on` 本就在集合内，取到就是它自己）与日/周切换（`on` 来自另一个视图，找不到精确
  匹配）。**`on` 必须是打开详情那一刻来源页面正在看的期次**，不是「今天」——从历史查询
  结果打开详情却看见比来源页面更新的数据，是「静默跳到最新期」的一种形态。
- **`latest_available` 与锚定期当期可能不是同一天，前端必须分别标注日期。** 锚定期本身
  若是掉榜/未收录/未导入，`latest_available` 会取更早的一个在榜点；把它显示成「当前
  排名」是这条口径唯一容易踩的坑。
- **权限接受 `analysis:view` 或 `keyword:view` 任一**（`simple_auth.require_any_permission`），
  不新增权限点。这是刻意的例外：端点只读一个公共 `keyword_id` 的历史排名，不携带任何
  域私有数据，两个入口各自的合法用户都不该被另一个域的权限矩阵挡住。**这个原语只在
  「端点跨域共用且不携带私有数据」时才该用**——像 `competitor_new` 那种读取用户私有
  关注关系的筛选，仍然是`check_permission` 按需叠加校验，不能用「任一满足」放宽。

## 顶栏菜单：按数据域命名，不设按排除法命名的容器

顶栏八项，名称统一四字，顺序固定：**机会中心 → 市场分析 → 词库工具 → 竞对监控 →
产品档案 → 风险核查 → 定价核算 → 数据管理**（机会中心是主动式工作流的入口排最前，中间
四项「看 ABA、竞对与产品数据」，风险核查次之，数据管理是唯一「喂数据/管数据」的，排最后）。路由与菜单在
`frontend/src/routes.ts`——它从 `.umirc.ts` 抽出来单独成文件是为了**可测**
（`src/routes.test.ts`；直接 import `.umirc.ts` 会经 `@umijs/max` 拽进 esbuild，
jsdom 下启动即崩）。

**新增一级菜单的判据是「它读哪个数据域」，没有一项匹配就新开一个。** 这里曾有一个叫
「工具箱」的一级菜单，定义是「不属于选品主线的其他东西」——名字说的是它不是什么，
因而不给新功能提供任何归属判据：下一个功能只能问「它像不像已经在里面的那些」，而那个
问题没有答案。它最后装了四个屏，两个读 ABA 事实表、两个读根本不存在的专利/商标库；
竞对监控没有放进去，就是它已经没有原则的证据。已拆成「词库工具」（原名「关键词工具」）
与「侵权查询」两项，「侵权查询」后来（issue #76）又改名「风险核查」。

**单页顶层（`/opportunity`、`/analysis`、`/competitor`、`/products`、`/pricing`）不得加二级菜单。** 布局是
`layout: 'mix'` + `splitMenus`，顶层节点一有子项就长出约 208px 的左侧栏。按数据域，
增长榜与词库本该并进 `关键词市场分析`（原名 ABA数据选品），正是这条否决了它——分析
查询屏是全产品最宽的表，横向裁掉的列找不回来（与「我的词库」「竞对监控」两屏拒绝侧栏
形态的是同一笔账）。机会中心的六个状态同理不拆二级路由，用页内 Tab 承载。

**机会中心（`/opportunity`，issue #76）是承接"确定性 Signal → 候选机会 → 验证推进 →
风险核查"这条主动式工作流的入口**，不叫「AI Agent」或「聊天助手」——那是技术实现，
不是用户任务。**权限点 `opportunity:view` 是当前唯一一个默认不授予任何内置角色
（含 admin）的权限点**，只有 `is_super=true` 能看到，即便某个用户角色码是 `admin`
也不例外——`role_code == 'admin'` 与 `is_super == True` 是两个独立字段（见
`user_crud.create_user`）。落地这条规则时踩过一个陷阱：`app/auth/permissions.py` 的
`ROLES["admin"]` 历来定义成 `set(PERMISSIONS.keys())`（动态取全部权限），若把
`OPPORTUNITY_VIEW` 直接塞进 `PERMISSIONS` 而不处理，admin 角色会自动继承它——因此
新增了 `SUPER_ONLY_PERMISSIONS` 集合，`ROLES["admin"]` 改为
`set(PERMISSIONS.keys()) - SUPER_ONLY_PERMISSIONS`，把这个权限点显式排除在"admin 隐含
全部权限"这条默认规则之外。当前页面只有导航与权限骨架，不发任何请求——信号计算、机会
评分、验证流程分别在 issue #7 / #13 / #24 / #25 / #38 / #54 / #55 里逐步接入，接入前
MUST NOT 展示编造的 0 值或结论。

**风险核查（`/ip`，审核待办 / 外观专利 / 商标查询 / 风险案例，原名「侵权查询」）只是占位屏**：仓库里没有
任何专利/商标数据，**不发请求、无后端端点**。因此它**没有权限点**，只要求已登录——给
没有端点的屏开权限点，就是再造一遍 `user:manage` 那种「勾了也不生效」的复选框。issue
#76 曾把"仅超管可见"列为这个菜单的一个可选项，但没有采纳——没有端点的屏，"谁能看"不是
一个真实的授权需求，与机会中心那种"有真实权限边界待将来接入端点"的情况不同。接上数据源、
有了真实端点再加权限点，那时是加法，不会留下失效的历史勾选。别为了「每个菜单都有
access」的整齐而补一个。

**四字导航（2026-09-05）**：导航简称不强制缩短业务页面标题。「市场分析」页面仍可显示
「关键词市场分析」。词库工具子菜单为「增长市场 / 我的词库」，数据管理为「文件导入 /
导入历史」。风险核查默认进入 `/ip/review`，原专利与商标路径继续有效。

产品档案（`/products`）与定价核算（`/pricing`）本轮只提供明确标注尚未接入的入口，
不发请求、不生成业务数据、不新增空权限点；真实端点落地时补服务端权限。
定价核算用「快速核价 / 核价记录 / 费用配置」页内 Tab，产品档案保持全宽。
产品档案统一关联风险审核与核价记录，两个业务域不能各自复制一份产品档案。

把它放进顶栏是自觉取舍：产品最空的一块因此成了最显眼的一块，换来的是「这块在做」这个
路线图信号可见。

旧地址 `/toolbox`、`/toolbox/growth`、`/toolbox/library` 保留重定向；两个占位屏的旧地址
**不留**——没有人会去书签一个写着「数据源尚未接入」的屏。

## 竞对监控（`/api/competitor/*`，权限点 `competitor:view`）

盯别人的店：把竞对卖家攒成关注清单，供后续的上新监控使用。**P0（关注清单）、P3a
服务端侧（Amazon 卖家 ASIN 上报，见下文独立小节）、P3a 客户端（浏览器插件，被动采集，
插件见仓库根 `extension/`）与 P2（分析查询屏「竞对新发现」筛选 + ASIN 三槽位扩展，见
「分析查询屏」一节）均已实现**（issue #56）；浏览器插件后台自动巡检（P3b）与 Keepa
拉取式接入（P3c）未开工——「竞对新发现」筛选在插件覆盖率不足时会长期少命中甚至没有
命中，这不是缺陷。方向 C（品牌级联查，卖家 → 品牌名 → `top1/2/3_brand_id`）因拿不到
「卖家 → 品牌名」这层映射，移出 P2 范围。

**插件的三处限制，改动前先读 `extension/README.md` 与对应变更的 design.md**（`openspec/
changes/add-competitor-browser-collector/`）：鉴权是**手动粘贴 token 的占位方案**，正式
方案（读网页会话 / 独立 Personal Access Token / 插件内登录）尚未拍板；DOM 选择器
（`extension/src/parse-storefront.ts`）**未经真实登录会话验证**，本仓库拿不到真实 Amazon
账号，只能靠人工在真机核实，见该变更 tasks.md 第 8 节；插件的网络请求依赖后端当前
`allow_origins=["*"]` 的宽松 CORS 配置才能不声明 `host_permissions` 就跑通——**收紧这个
CORS 之前，必须先给插件的来源开白名单**，否则表现是插件请求全部"网络异常"，而真正原因
是 CORS 被收紧，两者在插件界面上长得一模一样。

**两层数据模型，分层的判据是「这条数据属于谁」**：

| 表 | 层 | 说明 |
|---|---|---|
| `competitor_seller` | 共享 | 卖家主体，`(seller_id, site)` 复合主键。**MUST NOT 携带账号属性** |
| `user_seller_group` | 私有 | 用户自建的归类标签，`(user_id, name)` 唯一 |
| `user_seller_watch` | 私有 | 关注关系，`UNIQUE(user_id, seller_id, site)` |
| `competitor_watch_preview` | 技术 | 导入两步流的暂存，**替代引入 Redis** |
| `competitor_seller_asin` | 共享 | 卖家在售 ASIN，`(seller_id, site, asin)` 主键，只增不改 |
| `competitor_asin_report` | 共享 | 一次上报的观测元数据（不是快照），见下文独立小节 |

六张都是普通表，不分区、不参与保留清理、不被导入路径写入。

分共享层的理由是抓取代价：后续上新监控要按卖家抓商品，**一个卖家被十个用户关注也只该
抓一次**。把站点码或 `baseline_at` 挪进私有表就等于按人重复抓，这是整个分层唯一的意义。

**与词库（`keyword_group`）刻意不同的两点**，抄错了不会报错：

- **分组不绑站点。** 这条后来还多兑现了一次：独立站监控落地时，同一个分组直接就能装
  Amazon 卖家与独立站两类成员，分组表一行代码都没改。词库的分组绑站点，因为 `keyword_id`
  不含站点；这里 `CompetitorSeller`
  已由 `(seller_id, site)` 唯一确定站点，分组只是标签，允许跨站点。
- **删组不取消关注**（`group_id` 外键 `ON DELETE SET NULL`，落到「未分组」）。分组是标签，
  关注是「我在盯这个卖家」这件事本身，误删一个标签不该连带把盯了三个月的清单清空。

**解析器 `seller_ref_parser.py` 是纯函数，书签导入与手工粘贴共用一份。** 用户从浏览器
复制来的必然是带 `ds=` 会话签名的长 URL，两条入口的容错要求完全一样，各写一份的表现是
「文件导进去了，同样的链接手工粘贴却报错」。六种形态：三种卖家链接（`i=merchant-items&me=`
/ `/sp?seller=` / `at-a-glance.html?seller=`）、裸卖家 ID，以及两种**明确拒绝并附原因**的
（品牌店铺页 `/stores/`、商品详情页 `/dp/`）——它们不静默丢弃，否则用户只会看到「2657 条
进来了 2400 条」而无从知道少的是哪些、为什么。

**站点识别的优先级是 `marketplaceID` > 域名 TLD > 调用方给的 `fallback_site`。**
裸卖家 ID 天生不带站点信息，**永远**需要那个兜底；拿不到就拒绝，**不猜 `DEFAULT_SITE`**
（与 `normalize_site_code` 同一条纪律：非法/缺失站点报错不回退）。`ds=` 等会话签名参数
在解析处即丢弃，从不读取、存储或用于重建请求。

**导入是预览 → 确认两步，配额在预览阶段就判定。** 两条约束：

- **超配额整批拒绝、不截断**，且判定发生在预览而非确认——让用户看完 2657 条的预览再告诉
  他「超了」，那次预览就白做了。上限见 `.env.example` 的 `COMPETITOR_*`
  （`MAX_WATCH_PER_USER=3000` / `MAX_IMPORT_BATCH=5000` / `PREVIEW_TTL_SECONDS=600`）。
- **确认有 `append`（默认）与 `replace_group` 两种模式**。后者必须带 `target_group_id`，
  先清空该组再写——它是「这个组以我这份书签为准」，不是追加，界面必须说明白。

预览暂存进表而不是 Redis，是为了不给这一个功能引入一个新的基础设施依赖；过期行清理
**挂在 `import_queue.py` 已有的轮询循环上，不新开定时器**（新开一个就要各自处理多 worker
重复执行的问题）。书签文件约 2MB，同步读进内存即可，**不走 OSS 直传那一套**——那是给 GB 级
CSV 准备的，用在这里是过度设计。

**权限点 `competitor:view` 是独立的，不复用 `keyword:view`。** 与「外观专利」那种占位屏
相反，这一屏有真实端点、真实数据、且每个用户的关注池互相独立，是一个可以真正被授予或
不授予的能力。私有性靠每条查询里的 `user_id`，别人的分组/关注/预览一律 **404 而非 403**。

### 竞对卖家 ASIN 上报（`/api/competitor/asin-reports`，issue #56 P3a 服务端侧）

**推模型，不是拉模型。** 采集端（浏览器插件，不在本变更范围）主动 `POST` 它在某卖家页面
看到的 ASIN；后端**不实现**拉取式的 `fetch_listings(seller_id, site)` 协议——那会迫使
接收端伪造一个"本次拉取覆盖了多少"，而这个问题在推模型下没有答案。上报只表达一件事：
**在某个时刻，上报者的浏览器里见到了这些 ASIN 属于这个卖家**，不承诺目录完整性。

**由此推出三条边界，缺一条都会在某个时候制造出一批安静的假数据：**

1. **不做下架判定。** "这次没见到"与"已下架"在数据上无法区分。与独立站「下架判定只在
   `catalog_complete` 为真时做」是同一条纪律的必然结论——独立站有三道完整性判据才敢判，
   被动采集一道都没有。
2. **不存逐次目录快照。** 快照记录的是「运营那天翻了几页」，而它与「这家店只有 18 个品」
   长得一模一样。改为 `competitor_asin_report` 存一次上报一行的观测元数据（谁、何时、
   哪个卖家、多少条、采集时的配送地区、页面上下文），回答的是「数据有多新、怎么来的」，
   不是「这家店有什么」。
3. **产出的是「新发现」，不是「上新」。** 被动采集证明不了商品何时上架，只能证明"我们
   何时见到"。界面措辞一律「发现于」/「近 N 日新发现」，**0 只表示"已采集范围内没有新
   发现"，MUST NOT 读作"这家店没有上新"**。独立站有数据源给的 `published_at`，才继续称
   「上架于」——两条线的措辞差异是数据来源差异的直接结果，不为界面整齐而统一。

**基线 = 第一次上报，一旦落定不再改动。** 某卖家的首次上报把 `baseline_at` 落成本次
服务端事务时间，该批 ASIN 照常入库但不进上新流；此后只有 `first_seen_at > baseline_at`
的才算新发现。**改动基线是本路径唯一能造成大面积假数据的方式**——会让基线批次的商品在
下一次查询里整批涌进上新流，且没有任何地方会报错。实现上落基线的语句是
`WHERE baseline_at IS NULL`（不是 `SET = COALESCE(baseline_at, now())`）：两者保证等价，
但前者常态下根本不写——99% 的上报是"基线早就有了"，与 `dim_product` 那条
`IS DISTINCT FROM` 是同一笔账。**基线与本批 ASIN 必须落在同一个事务里**：Postgres 的
`now()` 取事务开始时刻而非语句时刻，两者因此严格相等，配上"新发现"判据的严格大于，
首批才能恒不算新发现——拆到另一个事务里落会让两个时刻不再相等，首批反而全部漏进新发现。

**时间的权威只有服务端。** `baseline_at` 与 `first_seen_at` 一律取服务端事务时间；上报体
可携带的 `captured_at` 只落进 `competitor_asin_report` 作审计，**MUST NOT 参与任何判定**。
客户端时钟不可信也不可验证，让它左右判定意味着一台时间偏了的机器就能把整批老品的首次见到
时刻推到基线之前（整批消失）或推到未来（整批涌进新发现），两种错误在界面上都长得像正常
数据。

**幂等靠两把锁，缺一把只挡住一半：**

- `request_id`（客户端生成，**必填**，按 `(user_id, request_id)` 唯一）——命中即回放原
  记录、不新增行、不推进「最近采集于」。唯一性按用户分域是因为跨用户撞键会让后一个用户的
  上报被吞掉、并把前一个用户的计数回放给他。
- `request_hash`——只有幂等键不够：它只能认出"这个键来过"，认不出"这两次是不是同一次
  观测"。摘要覆盖 `seller_id` + `site` + 规范化去重排序后的 ASIN 集合 +
  `captured_at` + `ship_to_region` + `page_context`（**只排除标题与图片**——易变的展示
  属性，且只在首次落定、不影响写入结果）。命中键但摘要不一致，说明这个键被用去报了另一次
  观测，返 **409 `competitor.idempotency_key_reused`**，不写入也不回放——静默回放会把
  一次真实观测丢掉。**这条摘要范围曾被写反过一次**（把三个审计字段也排除，理由是"重读
  DOM 后重试会撞出假 409"）：那不是重试，真正的幂等重试重发的是已经序列化好的原请求体，
  三个字段逐字节相同；重新采集产生新的 `captured_at` 就是另一次观测，本该带新键。判据是
  两种误判代价不对称——多返一个 409 响亮可自愈，漏判成重放则一次真实观测安静地凭空消失。

**每行在售关系记录首次引入它的上报**（`first_report_id`，`ON DELETE SET NULL`）。共享层
一个人写错、全体看见，没有这个指针只能在"全删"与"不管"之间选，它比逐次快照便宜两个数量级
却保留了同样的溯源能力。上报者账号被删时指针置空而非连带删除——商品行是别人也在用的共享
数据。

**上报只接受上报者本人已关注的卖家，整个请求拒绝（不是逐条）。** 一次上报只对一个
`(seller_id, site)`，共享数据若不限制写入来源，任何持有令牌的用户都能污染其他人看到的
数据。`user_id` 一律取自访问令牌，请求体里的同名字段被 schema 无视（`extra="ignore"`，
不用 `extra="forbid"`——后者变成 422 反而暗示"这个字段是被认识的"）。

**单条 ASIN 格式非法逐条拒绝、其余照写；条数超限整批拒绝、不截断。** 判据是"这样做会不会
造出一份无法与正常数据区分的残缺记录"：超限意味着采集端手上比送出的多，写入即沉默截断，
上限见 `.env.example` 的 `COMPETITOR_MAX_REPORT_ITEMS`（默认 200）；单条坏数据丢弃不制造
完整性假象，原因回传能让插件作者发现选择器取错了元素。同请求内重复 ASIN 去重不拒绝——
同一个品出现在页面两处是常态。

**零条有效 ASIN（请求为空，或全部条目因格式非法被逐条拒绝）整批拒绝（422），MUST NOT
落定基线。** 这条不是"单条格式错"的推论，单独成立：放行的话，首次上报会落一个不含任何
ASIN 的"空基线"，下一次正常上报时该卖家的全部存量商品都晚于这个空基线，被整批误判成新
发现——这个错误不会报错，界面表现只是新发现数突然涌出一大批。判据不分是否已有基线：即便
基线已经落定，一次零有效条目的上报本身也不携带任何可用信息，没有受理它的理由。

**非法站点码返回 422，不是 500。** `normalize_site_code` 抛 `UnknownSiteError`，
`asin_service.submit_report` 必须捕获并转译——不转译就会被全局兜底处理器当成内部错误。
这条检查在授权检查之前，因为一个连站点码都不合法的请求，谈不上"是否关注了这个卖家"。

**上新流在 SQL 里合并两类来源，不在应用层归并两页**（`store_crud.new_arrivals_stmt` 对
两侧统一投影后 `union_all`）：各来源自己的 offset 与合并后的全局序号对不上，翻页会重复或
漏条目，表现是"某条上新出现两次"。**排序必须带次级键**
（`source, source_key, site, product_key`）：`published_at`/`first_seen_at` 精度有限、同一
时刻多条是常态，没有次级键时同一行可能在两页各出现一次——**这条在小数据量下测不出来**
（去掉次级键后集成测试照样绿，Postgres 的顺序碰巧稳定），得靠比 `ORDER BY` 子句本身的
结构性断言。**`site` 必须进投影与次级键，不是可选项**：共享层的真实粒度是
`(seller_id, site, asin)`，同一 seller_id 跨站点卖同一 ASIN 是常态；漏了 `site`，这两行
在响应身份、前端 `rowKey`、排序键上会撞成"同一行"——`(source, source_key, product_key)`
三者都相同，翻页时互相顶替。独立站没有站点这回事，该列对 DTC 行恒为 NULL，两侧投影列数
仍须相等（UNION 的硬要求）。卖家清单的「近 N 日新发现」同样有一处容易写错且错了不报错的
地方：相关子查询里 `count(*)` 恒返回 0 而不是 NULL，**无基线时必须显式 `CASE WHEN
baseline_at IS NULL THEN NULL` 罩一层**，写成 `COALESCE(...,0)` 会让「待建立基线 vs
监控中」的界面故事就地塌掉。

**「点开一行看详情」不在本变更范围内**（两类来源都没有这个交互，不是 Amazon 分支特有的
缺口），已从 `competitor-storefront-watch` spec 的 delta 里移出、记在 issue #65：独立站
缺 UI 入口去承接已有的变体/价格历史，Amazon 侧连"最近一次采集时刻"这个数据能不能按 ASIN
维度取到都还没定（当前 schema 只记了每个 ASIN *首次*被哪次上报引入）。

**`competitor_seller_asin`/`competitor_asin_report` 不参与按月保留清理**（不登记进
`PARTITIONED_TABLES`）：首次见到时刻正是新发现判定的依据，越久越有价值，三个月窗口是为
八亿行的 ABA 事实表定的，与本能力量级无关。

**配送地区仍不用于过滤或存活判定，只留档。** 实测 Amazon 店铺搜索结果按操作者账号收货
地址过滤（2026-08-31 抽样 120 家日本地址书签卖家，92 家显示无货，抽查 30 家却有 16 家仍
在正常卖货，真实存活率回推约 64%），因此在非目标市场地址下"没见到"是账号设置的产物、
不是事实。上报体的 `ship_to_region` 存下来只为出问题时能分辨"这批数据是在什么账号设置下
采的"——这个信息事后补不回来，与独立站那边"币种事后补不回来"是同一类。

### 独立站监控（Shopify，`/api/competitor/stores*`）

与 Amazon 卖家**共用分组、权限点与上新流，不共用表与字段**。分的是数据与采集，合的是
归类与呈现——运营的心智是「我在盯这一批竞对」，不是两套体系。

**为什么先做这条**：它没有未决前提。`/products.json` 是公开端点，服务端一个 HTTP 请求即可，
不需要插件、浏览器、用户登录或令牌；而且**上架时间是数据源直接给的事实**（`published_at`，
精确到分钟），首刷即有历史，一条基线都不用建。

| 表 | 粒度 | 写入模式 |
|---|---|---|
| `competitor_store` | 站点 | 状态列（`last_fetch_at` 记**尝试**不是成功） |
| `competitor_store_product` | 商品 | 只增不改 |
| `competitor_store_price` | **变体** | **仅在取值变化时追加** |
| `competitor_store_rank` | 商品 × 日 | 每日追加，只存前 N 名 |
| `user_store_watch` | 关注关系 | 私有，`group_id` 指向**同一张** `user_seller_group` |

五张普通表，**MUST NOT 登记进 `PARTITIONED_TABLES`**——登记即被保留清理按
`HOT_WINDOW_MONTHS` 整月 DROP，而价格与名次的历史越长越值钱；那个三个月窗口是为八亿行的
ABA 事实表定的。

**七条不可违反的约束**，每条都对应一个「错了不会报错」的失败形态：

1. **下架判定只在 `catalog_complete` 为真时做。** 完整 = 每页 200 + 翻页因空页自然终止 +
   **取到的商品数不少于 `/meta.json` 声称的 `published_products_count`**（第三道最硬，实测
   三家店与逐页累计完全吻合）。撞上 `DTC_MAX_PAGES` 必须记为不完整。不完整时照常写入见到的
   商品（只增，永远安全），但一次网络抖动把整店判成清仓是这条路唯一能造成大面积假数据的方式。
2. **新品判定看 `product_id` 在不在库里，不看 `published_at`。** 商户批量重新发布会让后者
   整体跳到当日，据此判定会伪造出一批假上新。
3. **上新流按 `first_seen_at > first_fetch_at`，「近 7 日上架数」按 `published_at`。** 两个
   口径不同不是疏漏：前者答「纳入监控后新出现了什么」，后者答「这家店最近一周上了多少新品」。
   `first_fetch_at` 用 `COALESCE` 保护，一旦落定不再改动——改了会让首刷那批涌进上新流。
4. **价格只在 price/compare_at_price/available 任一变化时才写。** 每日全量快照是约 30 倍写
   放大且其中 99% 与前一日相同，与 `dim_product` 那次 `IS DISTINCT FROM` 是同一条纪律。
   表里**没有「当前价」列**——当前价就是 `observed_at` 最大的那行。
5. **币种必须存，且补不回来。** 实测 17 个站点里已有两种（blessrich 是 GBP，其余 USD），
   而 `/products.json` 的数字不带币种。取自 `/meta.json`，该端点同时充当 Shopify 判据。
   界面未知币种时**只显示数字不臆造符号**。
6. **销量只有店内相对名次，没有绝对数字。** `/products.json` 无销量字段；同名的
   `/collections/all/products.json` 实测**忽略 `sort_by`**，只有 HTML 版
   `?sort_by=best-selling` 生效（每页 20 个商品、每个 handle 出现 3 次，去重是必需的）。
   界面 MUST NOT 换算或呈现为销量/销售额，也不可跨店比较。
7. **四种状态互不合并**：待首次拉取（没到点，不是坏了）/ 未开放商品接口（商户自己关的，
   **真实形态是 HTTP 404**，重试也不会好）/ 抓取失败（带连续次数）/ 数据不完整。
   畅销榜解析异常**独立成标记**，与目录状态互不牵连，且绝不显示成「该站销量榜为空」。

**加入入口有两个，共用一份域名规范化**：单条表单（长期主路径）与批量粘贴（一次性初始
装载）。批量**一步完成，不做预览 + 确认**——判据是这个操作可不可逆：卖家侧那套两步流
存在的理由是 `replace_group` 会清空整组重建、且 2657 条来自书签文件用户不知道里面有
什么，这里两条都不成立（用户粘的是自己知道的域名，加错了取关一次点击且数据还留着）。
批量结果按**输入行**组织并**逐条附原因**，MUST NOT 只报「N 条失败」——三类原因对应的
动作完全不同。探测并发有上限（信号量 8，共用同一个 `AsyncClient`），单次条数超限
**整批拒绝不截断**。

**拉取挂在 `import_queue.py` 已有的轮询循环上**，按 `last_fetch_at` 状态列认领
（`FOR UPDATE SKIP LOCKED`，**不用全局咨询锁**——瓶颈在网络不在数据库，全局串行化只会让
站点数增长时排队时间线性膨胀）。**网络那段 `await` 在事件循环上，只有数据库写入走
`run_in_threadpool`**：那 32 条线程与 bcrypt 登录共用，一家店抓 8 秒就占住一条线程 8 秒。

**本变更不做取关后的 GC。** issue #56 定过「保留 7 天」，但那是按每天全量轮询的量写的；
被动采集量小一到两个数量级。这里写明是**有意不做**，不是漏了。

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
│   ├── keyword/       # 关键词工具查询层（增长榜 growth_* + 词库 library_*）
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
  权限点有六个：`analysis:view`/`analysis:export`/`upload:import`/`keyword:view`/
  `competitor:view`/`opportunity:view`；内置角色
  admin/operator/analyst（见 `app/auth/permissions.py`）。逐路由 `require_permission(code)` 授权，
  is_super 兜底。前端在 `access.ts` 按权限码显隐菜单。**`opportunity:view` 是例外**：默认不
  授予任何内置角色（含 admin），只有 `is_super=true` 能用，`permissions.py` 的
  `SUPER_ONLY_PERMISSIONS` 集合把它排除在"admin 隐含全部权限"这条默认规则之外
  （见上文「顶栏菜单」一节机会中心部分）。
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

**钉钉单点登录（`/api/auth/sso/*`，全部免鉴权）**：员工扫码即可登录，不必在本系统单设
密码。走**钉钉开放平台的企业内部应用**，OAuth2 授权码流程 + 浏览器扫码；**不经 IDMesh
等身份中台**（开放平台的登录与通讯录能力在钉钉免费版即可用，零额外费用）。六条要点：

- **这套不是 OIDC，尽管授权参数里的 scope 叫 `openid`。** 钉钉换回来的是 accessToken，
  没有 id_token、没有 JWKS、没有 discovery 端点——**接不上任何 OIDC 依赖方库**。而且
  `/v1.0/contact/users/me` 只给个人身份（unionId、nick，文档明写「无手机号和企业相关
  信息」），拿企业内 userid、姓名与在职状态要**另一套企业 accessToken** 调通讯录接口。
  所以 `dingtalk_client.py` 里是**两套凭证**，企业那套带进程内缓存 + `asyncio.Lock`
  （没有锁，并发登录各换各的、互相覆盖，钉钉侧还会限流，表现为随机登录失败）。
  另注：`oapi.dingtalk.com/connect/oauth2/sns_authorize` + `sns/getuserinfo_bycode`
  那一套已于 2021-12 迁入钉钉的历史文档区，**不要采用**。
- **账号只按 `(external_idp, external_id)` 匹配，`external_id` 存 unionId。**
  这是本功能唯一实质的攻击面：若按用户名自动绑定已有账号，任何能改自己钉钉 userid 的人
  都能把它改成 `admin`，扫一次码就接管超管。用户名撞了只说明重名、**不说明是同一个人**，
  撞了就加数字后缀。存 unionId 而非企业内 userid，是因为后者管理员可改、离职再入职会换
  ——变一次即老账号成孤儿、当事人被当新人重新开号、角色全丢，且不会有任何报错。
  代价是同一个人的本地账号与钉钉身份**不会自动合并**，会并存两个账号；这是有意的。
- **`is_super` 恒假，无例外。** 用户管理是不可授予的能力（`require_super` 把守），
  不能由外部身份源决定谁拥有它。超管仍只能由 `scripts/seed_admin.py` 产生。
  首次扫码自动建档，角色取 `SSO_DEFAULT_ROLE_CODE`（默认 `analyst`），**不从钉钉部门/
  职位映射**——那会让「谁能导入数据」取决于通讯录怎么编排。**登录不回写已建档账号的
  属性**（回写要处理用户名唯一约束的连锁影响，收益只是显示名跟随改名）。
- **离职同步靠登录时反查，没有定时任务**：`topapi/user/getbyunionid` 查不到 unionId 即
  拒登。这条查询本来就在链路上（要拿 userid 才能取姓名），所以离职同步是白送的。
  已知代价：**离职前签发的 JWT 在过期前仍然有效**（当前 24 小时）。
- **本地密码登录保留，但只对本地账号有效。** SSO 账号 `hashed_pwd` 存 NULL，
  `authenticate_user` 复用既有的 `_DUMMY_PASSWORD_HASH` 路径把它挡掉——**不要新增
  「`hashed_pwd is None` 就提前返回」的分支**：提前返回把这条路径压到毫秒级，
  「这个用户名是不是 SSO 账号」就能靠响应耗时逐个试出来，正是那个哑哈希当初要堵的侧信道。
  保留它是钉钉侧不可用时的唯一逃生通道。
- **`SSO_ENABLED=false` 时端点一律 404（`sso.disabled`），路由仍然注册。** 按开关条件
  注册路由并不能让它变得可热切换（配置是启动期读入的），只会让这段行为测不到。前端靠
  免鉴权的 `GET /sso/status` 决定要不要渲染钉钉按钮——不这么做，每次进登录页都要发一个
  注定失败的请求。开启时凭证缺任一项**启动失败**；`SSO_DEFAULT_ROLE_CODE` 不在角色表里
  同样启动失败（启动期检查，需连库），但**数据库不可达不中断启动**——为一个配置校验让
  应用在数据库抖动时起不来，比这个配置错本身更糟。

前端入口在登录页（`pages/Login`）与过场屏 `pages/SsoCallback`（路由 `/sso/callback`，
`layout: false`、**不挂 access 守卫**——此刻还没有令牌）。登录跳转那段等 `currentUser`
落位再 push 的竞态修复抽在 `hooks/useLoginRedirect.ts`，**两屏共用一份**：它的正确性全在
那段注释的推理里，复制出去的副本会各自漂移，而出错表现是偶发的闪回登录页或 403。
外域跳转走 `utils/redirect.ts` 的 `hardRedirect` 而非 `history.push`（后者在自己的路由表
里找不到钉钉地址，会落到 404 屏）。

**回调域名配置与真机扫码无法靠 code review 确认**，用 `uv run python scripts/check_dingtalk_sso.py`
实测——它按真实登录顺序跑一遍并逐项断言，退出码非 0 即有项未通过。两类问题只有它能发现：
重定向 URL 与开发者后台登记的不一致（表现是扫码后停在钉钉页面，本系统这边**一条日志都
没有**），以及应用缺通讯录读权限（前面几步全过、直到反查 userid 才失败，而那个失败长得
跟「这个人离职了」一模一样）。

**后台 UI 是独立的 AntD Pro SPA**（`frontend/`，Umi Max + Ant Design + ProComponents），
本服务只提供 JSON API（amis 后台已删）。生产由 nginx 托管 SPA 产物 + 反代 `/api`。

**分析查询屏（`frontend/src/pages/Analysis/`）分三段**：顶部作用域栏（站点/视图/日期/检索框/
立即查询）、中部过滤区（左类目多选、右结果过滤）、底部 ProTable（`search={false}`）。

**状态编排拆成三个 hook，依赖方向单向**（issue #69 阶段二）：`useAnalysisSession`（会话快照、
复原短路、滚动位置）← `useAnalysisScope`（站点/视图/日期与选项清单）/ `useAnalysisQuery`
（草稿态与生效态、排序、总数、导出、表格请求）← `index.tsx`（只留跨模块协调与 JSX）。
**三个 hook 之间没有直接 import**：作用域失效要清筛选那条耦合，由 `index.tsx` 把
`clearCategoryFilters` 经回调接给 `useAnalysisScope`；组装查询体要读作用域那条，则是把
`scope` 草稿**按值**传进 `useAnalysisQuery`。两个方向都不许改成 hook 之间互相 import——
类目在「选项清单」与「用户勾选」两个身份下同名却生命周期相反，合回一处就又要靠上下文
判断「这个 categories 是哪一个」。三条约定：

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
  字段，判定结果必须回显），过滤区不再重复提供。**ASIN 按等值匹配、不是子串**（大小写与
  空白由 `AnalysisSearchRequest._normalise_asin` 抹平），因此 `dim_product.asin` 上**没有也
  不要加** trgm 索引——那条正则只有整串才路由到 asin 字段，片段到不了后端，而 GIN 索引要由
  每次导入的每一行写入维护（生产上 409 MB，issue #49）。那条正则是**路由启发式不是校验**：
  德国站图书的 ASIN 就是 ISBN-10、不以 `B0` 开头，所以后端只规范化、不校验格式。**「重置条件」清成默认（含只看新词）而非
  清空**，因此界面上没有一键回到「全部词」的入口——加一个「恢复默认」按钮会与「重置」
  语义相近，用户得先分辨差别才能选对，正是这条约定要避免的形态。想看全量榜单就把新词
  开关拨到「全部」，多一次点击是自觉代价。**「重置条件」还连带删除用户保存的默认条件**
  （否则重置完、下次登录又回到那组他刚明确不要的条件），**不做二次确认**——需求方的明确
  选择，已知代价是删除不可撤销而这个按钮看起来只是改改表单。它只改筛选，不动站点/视图/排序。
- **份额是 0~100 的百分数**，界面、快捷档位、接口一路不做换算；**排名变化保留原始符号**
  （负数=上升），档位用中文语义承担可读性——界面翻转符号会让用户拿表格里的数字回填筛选时
  得到相反结果。
- **「竞对新发现」是与其余筛选维度 AND 组合的独立布尔开关**（`competitor_new`，与 `is_new`
  同构，`None`/未勾选 = 不筛，issue #56 P2）。命中口径：该词前三商品槽位任一 ASIN，是
  当前用户在当前站点关注的卖家**近期新发现**的 ASIN——判据与卖家清单「近 7 日新发现」
  （`watch_crud.NEW_DISCOVERY_WINDOW_DAYS`）同构并复用同一个窗口常量：
  `first_seen_at > baseline_at`（基线批次不算）且落在窗口内。**MUST NOT 只判断"卖家是否
  上报过这个 ASIN"**——`competitor_seller_asin` 不做下架删除（`asin_model.py` 的既有边界），
  首版实现漏了时间限定，表现是这个开关命中卖家有史以来上报过的全部商品（PR #66 复审 P1）。
  **界面标签是「竞对新发现」，不是「竞对新品」**——被动采集证明不了商品何时上架，只能证明
  我们何时见到，「上新」及其近义词在这条数据线上一律不用（与上文竞对 ASIN 上报一节
  "产出的是「新发现」，不是「上新」"同一条纪律；PR #66 三次复审 P2 修的就是"口径改了、
  名字和提示没跟着改"）。请求字段沿用 `competitor_new`，读作 new *discovery*。
  **界面提示必须同时说出两个空结果成因**：插件未上线（数据没进来）与商品都在 7 天窗口外
  （数据进来了但过期）——只说前者，等插件上线后用户会照着一条已不成立的原因排查。
  `baseline_at` 为 `NULL`（卖家尚未建立基线）时
  `first_seen_at > NULL` 恒为 SQL NULL（假），天然不命中，不需要额外的空值兜底。
  `user_id`/`site` 都取自访问令牌/作用域，不取自请求体。ASIN 精确匹配同一批改动里从只命中
  `top1_product_id` 扩到三个商品槽位任一命中（与品牌/类目同一形状），`product_title` 仍只
  命中 top1、未扩展。**`competitor_new` 必须计入 `analysis_crud.request_has_filters()`
  的元组，且是 `req.competitor_new or None` 不是裸值**——裸值会让漏掉计入这一项时
  "确实过滤了行、总数却仍报告站点当期总词数"（`brands`/`categories`/`is_new` 同一陷阱），
  而不用 `or None` 会让 `competitor_new=false` 被误判成"带筛选"（这个字段只有两态，
  `_apply_filters` 是 `if req.competitor_new:`，`False` 与 `None` 的查询结果完全一样，
  PR #66 复审 P2）。**`competitor_new=true` 额外要求 `competitor:view` 权限**，只有
  `analysis:view`/`analysis:export` 不够——它读的是竞对监控域的私有数据，两个数据域权限
  独立；`/search`、`/export` 都要在触达数据库之前用 `simple_auth.check_permission` 拦
  （PR #66 复审 P1，同批漏掉的第二处），前端按 `access.canViewCompetitor` 同步隐藏开关，
  但那只是体验层，真正的边界在后端。界面开关旁必须提示"近 7 日内首次见到、且晚于该卖家
  基线批次的商品；数据依赖浏览器插件采集，插件未上线前可能长期无命中"——空结果有三个
  成因而它们在界面上长得一模一样：插件客户端（issue #56 P3b）尚未开工、某个卖家还没被
  上报过两次（首次上报即基线，不算新发现）、见到的时刻已经出了 7 天窗口。提示只说其中
  一个，用户就会照着一条不成立的原因去排查。方向 C（品牌级：卖家 → 品牌名 → `top1/2/3_brand_id`）数据源
  不存在（拿不到"卖家 → 品牌名"这层映射），移出本次范围。

  **`competitorNew` 必须计入前端 `FILTER_KEYS`**（`useAnalysisQuery.ts`）——它决定「本次结果不含
  掉榜行」提示的显隐，漏掉的表现是用户只启用这一个筛选、其余（含默认的「只看新词」）都
  关掉时，后端仍按带筛选路径排除全部掉榜行，界面却不提示，用户以为看到的是全量结果
  （PR #66 二次复审 P2）。**保存的条件读入草稿时必须按当前权限规范化**
  （`filters.ts` 的 `sanitizeCompetitorNew`）：保存的条件跨登录持久，若用户保存时有
  `competitor:view`、之后被收回，原样恢复会让首屏自动查询带着 `competitor_new: true`
  撞上后端权限校验（403）——而开关本身又因无权限被隐藏，用户看不到也无从关掉这个仍在
  生效的条件，只能猜测并重置全部条件。规范化点有两处（草稿初值、`resolveInitialScope`
  的首屏条件），`apply()` 额外做一次同样的兜底作为三处生效入口（立即查询/开始筛选/
  首屏自动查询）唯一共同必经之处，不依赖调用方各自记得清空这个字段（PR #66 二次复审
  P2，同批修复）。

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

# 钉钉单点登录。SSO_ENABLED=true 时前三项缺一即启动失败；关闭时端点一律 404
SSO_ENABLED=false
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
SSO_REDIRECT_URI=              # 指向 SPA 的 /sso/callback，须与钉钉后台登记的完全一致
SSO_DEFAULT_ROLE_CODE=analyst  # 首次扫码建档时赋予的角色，须存在于 role 表

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

## 流程重量：先问，别默认最重

**开工先问一句这件事要多重，默认取最轻的那档。** 三档：

| 档位 | 做什么 | 适用 |
|---|---|---|
| **直接做** | 不写 spec、不建 worktree，改完跑相关测试就提交 | 改样式、修一处逻辑、补一个测试 |
| **先对齐再做** | **一次**问清关键决策，复述一遍就动手；不写 spec | 需求有分叉但形态清楚 |
| **完整流程** | openspec 四件套 + worktree + PR | 新子系统、改契约、要长期留档的决策 |

判据是**这个决策以后还要不要被翻出来看**，不是改动大小。「账号按 unionId 还是 userid 匹配」
值得留档（改错了没有任何报错），「登录页加个按钮」不值得。

### 为什么要有这条

2026-08-19 的钉钉 SSO 变更（issue #36）事后统计：生产代码 949 行、测试 1106 行、
**我写的散文 1366 行**（openspec 672 + 提交信息 337 + CLAUDE.md 100 + PR/issue 正文 257）。
散文比代码多。

原因不是哪一步做错了，而是**默认把所有重流程叠满**：brainstorming 判成 architectural →
完整问答 + 方案对比 + 分段设计 → openspec 四份产物 → start-change 建 worktree 装依赖 →
TDD 逐函数红绿 → 每次提交跑全量门禁。每层单独看都合理，叠起来是写第一行代码之前先过一小时。
而这个判断全程由 agent 做，人没有机会说「不用这么重」。

### 四条具体约束

1. **澄清问题一次问完。** 一个 `AskUserQuestion` 最多带 4 个问题，**不要串行问 4 轮**——
   每轮都是一次完整的人机往返。superpowers 的 brainstorming skill 规定「一次一个问题」，
   这条与它冲突时**以本文件为准**（用户指令优先于 skill）。

2. **篇幅按「反事实是否显然」定，不要一律拉满。** 本文件这种「每条都解释不这么做会怎样」
   的密度，是给**会被反复翻阅的约定**用的。一次性脚本、测试辅助函数、`catch` 这类改动
   不需要。提交信息 3–5 行说清「改了什么、为什么」即可；只有当「为什么这么写」不显然、
   且写错了不会报错时，才值得展开。

3. **先验前提，再设计。** 有没有那个租户 / 权限 / 数据源 / 收不收费——先问或先查。
   SSO 那次先按 IDMesh + OIDC 出了完整方案，事后才发现收费情况查不实，整段推翻重做。
   同理排查缺陷时**先读失败的用例源码，再提假设**：看见 `findByText` 超时就去调超时，
   改完还是红，回头才发现那条用例用的是同步 `getByRole`，跟超时无关。

4. **测试按影响面跑，全量只在提交前一次。** 前端全量约 97 秒且随用例数增长；
   `pnpm test src/pages/Login` 是 7 秒。`pnpm exec vitest related <file>` 走真实模块图，
   对叶子文件能省 9 倍——但 `.umirc.ts` / `vitest.config.ts` / `src/test/setup.ts` 这类
   **不在模块图里却影响所有测试**的文件它会匹配出 0 个，那种必须跑全量。

## 跨 Agent 交付流程：Issue 是交接契约，PR 是审核包

默认由用户在 **Codex 桌面端**讨论需求；讨论收敛后，先创建 GitHub Issue，再由
**Claude Code** 实现、**Codex** 审核。工具的分工不是约束用户必须在哪个界面思考，而是让
需求作者、实现者与审核者各自有清晰的事实来源：**不要要求下一个 agent 重读上一段完整聊天**。

完整流程如下；小改动仍按上一节的「直接做」或「先对齐再做」分流，不为流程而流程：

```text
Codex 桌面端讨论 → GitHub Issue → Claude Code：OpenSpec + worktree + 实现
→ PR + CI → Codex 审核 → Claude Code 修复 → Codex 复审通过
→ 合并至 v2 → 手动关闭 Issue + 删除分支/worktree
```

### 1. Issue：跨工具的唯一交接物

Issue 不得只有标题或聊天摘要；至少写明**背景与目标、可验证的验收条件、非目标、已决定的
关键口径/约束**，以及仍需在 OpenSpec 中落定的决策。这样实现者读取 Issue、仓库约定与
OpenSpec 即可开工，不依赖任何未共享的会话上下文，也避免把整段聊天、命令日志复制进去消耗
上下文。

### 2. 实现与 PR：把契约和证据一并交付

Claude Code 先按 Issue 判断流程重量；需要长期留档的变更走 OpenSpec，并遵守下文的认领与
仓库外 worktree 规则。PR 是给审核者的完整审核包，必须链接 Issue，并包含：对应的
`openspec/changes/<id>`、本次 spec delta、实现摘要、已运行验证及其结果、已知未覆盖风险。

### 3. 审核与修复：独立视角，但不越权改分支

Codex 审核 Issue / OpenSpec / PR diff 是否一致，并检查数据正确性、权限边界、迁移与测试缺口。
审核在独立的只读 checkout 或 worktree 中进行，**不得直接改 Claude Code 正在开发的 worktree
或 PR 分支**。发现分为「必须修复」「建议」「疑问」；Claude Code 对每项必须修复逐条修复，
或说明为何不修复并让审核者确认。只有所有必须修复关闭、相关 CI 与验证通过，才是审核通过。

### 4. 合并与收尾：v2 的 Issue 要手动关闭

审核通过后才合并进 `v2`。GitHub 只会在合并到默认分支时处理 `Closes #...`，而默认分支是
`main`，所以合进 `v2` 后必须**手动关闭 Issue**。确认合并后，才按下文的收工流程删除远端
分支与 worktree；评审未结束前保留它们，给 CI 红灯和后续意见留出修复位置。

## 工作分支是 `v2`，不是 `main`

V1 已完成使命并退役，V2 是完整重建（见上文「V2 数据模型」）。**所有工作基于 `v2`**，
`main` 停在 V1 最后的状态不动，等 V2 整体做完再一次性合并。

两条直接后果：

- **开分支从 `v2` 拉**（`git worktree add -b "change/$ID" ... v2`）。
- **`gh pr create --base v2` 的 `--base` 不可省。** 仓库的**默认分支是 `main`**，漏掉它会
  开出一个含上百提交、根本没法读的 PR。开完立刻 `gh pr view` 核一眼 base 对不对。

同理，`Closes #<n>` 在合并进 `v2` 时**不会触发关闭**（GitHub 只在合并进默认分支时关 issue），
需要手动 `gh issue close`。

**V2 不逐个发布**：各变更合进 `v2` 后不单独上线，整体做完再一次性发布。列剩余工作时不要
夹带发布步骤。

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

> **「暂存区是空的」这件事本身漏过两次，成因不同。** 一次是查错了仓库：会话在
> worktree 里往主仓提交，门禁查 worktree 的空暂存区，查了个空就放行——现已按**提交
> 实际落地的仓库**取（`target_repo` 解析 `cd X && git commit` 与 `git -C X commit`），
> 不按会话 cwd。另一次是查错了地方：`git commit -- <路径>` 提的是工作树内容，暂存区
> 可以完全是空的，而 `staged_files` 当时只在带 `-a` 时才补看工作树——于是本节推荐的
> 这条提交方式恰恰是门禁覆盖不到的那条（PR #66 的文案修复那次，20 个文件一条检查
> 没跑）。现已让路径式提交与 `-a` 走同一条路。
>
> 两次的共同表现是**没有失败、也没有拦截，输出与"全部检查通过"一模一样**。所以
> 门禁自己的回归测试（`test/test_pre_commit_hook_unit.py`）里，`staged_files` 那一组
> 守的不是"检查会不会失败"，而是"门禁到底看不看得见有改动"。

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
| `frontend/` | `pnpm lint` + `pnpm tsc` + `pnpm test` | ~30s（其中 vitest 占 ~28s） |
| 只有文档 / `openspec/` | 放行 | — |

**这些检查并发跑**（`run_checks`），墙钟是最慢那条而不是各条之和：前后端都改时实测
从 51 秒降到 42 秒。省不到"总和减最大值"那么多，因为它们要抢 CPU——vitest 本来就按
核心数起 worker，旁边再跑 pytest 会把它自己拖慢几秒。**失败报告按传入顺序排，不按完成
顺序**：否则同一份改动两次提交会得到不同顺序的报告，读的人无从判断是不是同一批失败。

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
- **`review-fix-cycle`** —— 取 Codex 审核意见、逐条核实（不是照评审的文字就改）、
  修在根源、补红绿验证过的回归测试、提交、回复、等 CI。假设已经在 PR 开着的分支/
  worktree 里，认领/建 worktree/归档不归它管，那是 `start-change` 的事。一次修复
  后评审可能再来一轮，回到第 1 步同样走一遍，直到通过。
- **`close-change`** —— 评审通过、要合并时用：合并、git 层面清理三样（细节见
  `start-change` 4.3）、**`openspec archive`**（本会话漏做过两次，合并完先做这步
  再去做别的事）、把没法自动验证的人工验收项转移到验收清单 issue 而不是让它随
  归档一起消失、判断并关闭触发这次变更的原始 issue、扫一遍还有没有别的残留（别人
  的 worktree 尤其是 Codex 的评审 checkout 不要碰）。
