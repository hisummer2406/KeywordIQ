# 分析、词库与数据模型

本指南是根目录 [CLAUDE.md](../../CLAUDE.md) 的按需附录，适用范围由主文件的必读表指定。
文中代码与命令路径均相对仓库根目录；主文件规定跨域红线，本指南保留该域的细则、理由和历史证据。
历史测量、上线状态与人工验证记录只证明记录时状态，不替代本次验证。

## V2 数据模型

V1 已完成使命并退役，V2 是完整重建。数据模型、导入、查询、界面均已落地，V2 已在阿里云
ECS 上线（2026-09-19，域名 `key.liangtong.online`，环境与接手状态见文末「当前部署交接」）。
旧稿里的「归档」层现在是按月保留清理（见「数据保留」），**数据库没有自动备份**
（2026-09-20 决定，见「Production Environment」）。

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

1. **事实表只追加，不原地更新。** 排名、上期排名、排名变化都在写入时算定。
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

- **日期选择器走 `site_data_stats`**（不在事实全表上实时聚合；在榜总数因类目黑名单已改为实算，见分析查询屏一节）；类目下拉走
  `category_stats`（按站点/周期/日期分片的普通表，2026-08-05 由物化视图改造而来，见
  迁移 `f5a6b7c8d9e0`——旧视图统计粒度比筛选粗一层、刷新代价与数据总量同阶，改表后
  导入只需重算当期切片）。查询代码在 `app/table/analysis/analysis_crud.py`（纯 SQL 组装）
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
`idx_fact_dropped_keyword_history`（`site, period_type, keyword_id, anchor_date`）
取数，不扫全站榜单。**掉榜段不走 `fact_dropped` 的主键**——主键是
`(site, period_type, anchor_date, keyword_id)`，`anchor_date` 排在 `keyword_id`
之前，单关键词历史查询里 `anchor_date` 是范围（`range=ALL` 或 30 天窗口）而非等值，
`keyword_id` 因此退化成扫描内 filter，代价随该站点/视图窗口内的掉榜行数增长，而不是
随目标关键词的历史行数增长（PR #87 复审 P1）。新建的
`idx_fact_dropped_keyword_history` 把 `keyword_id` 提到 `anchor_date` 之前，
把范围先收窄到单个关键词再扫描。五条要点：

- **五态分类，且不信任 `site_data_stats` 在保留下界之前的行。** `retention_service`
  现在会在同一次清理里，把 `site_data_stats`/`category_stats`/`brand_stats` 中报告
  日期早于保留下界的残留行一并清掉（issue #100，不再是「从不清理」）；但判定顺序仍然
  把 `report_date < retention_cutoff` 放在最前面、且**不查** `site_data_stats`——保留
  清理是按调度运行的，日历翻页到保留下界之后、下一次清理真正执行之前，天然存在一段
  分区还在但下界已经前移的间隔（这段数据仍是真实可查的，不属于残留），而这条判定顺序
  同时防住了另一种情况：万一某次清理漏跑、统计表短暂残留，也不会被误判成「已导入但
  未收录」，让用户以为那份数据还在、只是没收这个词。这是双重保险，不因为清理本身已经
  修好就删掉。其余四态：在榜（`RANKED`）/ 掉榜（沿用既有 `fact_dropped`
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

**数据保留：只保留最近三个月。** 由 `HOT_WINDOW_MONTHS`（默认 3）定义热窗口，报告日期
早于窗口下界的月度分区由保留清理**自动移出**——日报与周报统一适用同一窗口。移出是整
分区 `DROP`（连带站点子分区），不逐行删除、不产生死元组、不影响窗口内其他月份。清理逻辑
在 `app/table/analysis/retention_service.py`，运维入口是可定时触发的 `scripts/prune_retention.py`
（cron / 容器定时任务在低峰调用，务必先 `--dry-run` 核对、可 `--export-dir` 留档）。
**注意**：超过三个月的历史数据不再可在线查询（相对早期"12 个月满窗"是行为收窄）。

**分区移出必须同时清理 `site_data_stats`/`category_stats`/`brand_stats` 的对应残留
行**（issue #100）。这三张表支撑日期选择器、掉榜总数和类目/品牌下拉；在榜总数因类目黑名单走实算，详见前文；
只 `DROP` 分区不清理它们，查询侧会继续相信一个物理上已经不存在的日期还能查——复现是
`search` 对一个已被移出的日期返回 `success=true, total>0, data=[]`。清理**无条件
执行**，不依赖"这次是否真的 DROP 了分区"：否则本变更上线前已经产生的历史残留（分区
早被移出、统计行还在）永远等不到清理的机会。`scripts/prune_retention.py` 的早退判断
因此也要同时看这两件事，不能因为这次没有分区要移出就连残留统计一起跳过。

### Database Design

**Schema**: `analysis`

表结构见上文「V2 数据模型」一节（星型模型：`dim_keyword` / `dim_product` /
`fact_daily` / `fact_weekly` / `site_data_stats` / `import_batch`）。
V1 的 `amazon_origin_search_data` 单宽表与 `my_category_stats` 视图已随 V1 退役删除。

**`category_stats` / `brand_stats`**：普通表，按 (站点, 周期, 日期) 分片提供类目/品牌
下拉选项与计数，导入后由 `import_service._rebuild_filter_stats` 只重算当期切片（不是
物化视图——2026-08-05 迁移 `f5a6b7c8d9e0` 已经改造过一次，原因见该迁移文件说明：
旧视图统计粒度比筛选粗一层、且 `REFRESH` 代价与数据总量同阶）。

### Performance Optimizations

1. **Connection Pooling**: `database.py` 在模块级建了**两个**引擎，各自持有独立的池，
   服务的负载性质不同，故**分开配置**（`DB_*` 与 `DB_ASYNC_*`）。自
   `dockerize-selfhosted-infra` 变更起 PostgreSQL 自建、与应用同机部署（不再是独立的
   阿里云 RDS），自建 `db` 容器的 `max_connections`（`.env` 的 `PG_MAX_CONNECTIONS`，
   起点值 120）是硬顶，全局上限算式是：

   ```
   (DB_POOL_SIZE + DB_MAX_OVERFLOW) × workers        # 同步：导入 COPY、导出游标、迁移
   + (DB_ASYNC_POOL_SIZE + DB_ASYNC_MAX_OVERFLOW) × workers   # 异步：查询与鉴权
   ```

   `start.sh`/Dockerfile 固定 4 workers。当前取值 (2+3)×4 + (8+8)×4 = 84，为
   `PG_MAX_CONNECTIONS=120` 留出约三分之一余量给 psql/alembic/运维脚本。
   **应用与数据库现在共享同一组 4 vCPU**（不再是"数据库在独立机器"）：PG 同时真正能
   执行的查询仍受核心数限制，池开大不提升吞吐，只会把「快速可见的失败」换成「整体缓慢
   劣化」，且现在这组核心还要分给应用进程，比独立 RDS 时期更紧——这是本次迁移直接采用
   目标值起步、而非在旧值上"以后再降"的原因（`openspec/changes/
   dockerize-selfhosted-infra/design.md` 决策 5）。

   **`THREAD_POOL_LIMIT`（默认 32）与连接池无关，调池时不要跟着一起动。** anyio 的默认
   线程限流器被三类工作共用，只有第一类占连接：同步 `def` 端点（导出）与健康检查探活、
   bcrypt 哈希/校验（每次登录 ~247 ms，不占连接）、同步 `BackgroundTask`（导入，一条线程
   占住整场）。它曾按 `DB_POOL_SIZE + DB_MAX_OVERFLOW` 推导——同步池降到 2+3 后若仍然
   那样推导，限流器会塌到 5，一个导入在跑就只剩 4 条线程给全部登录，故两者已彻底拆开、
   互不联动。
2. **Query Optimization**:
   - 掉榜段行数读 `site_data_stats.dropped_count`（O(1)）。**在榜段行数不再读预存的
     `row_count`**：类目黑名单让可见行数小于整切片行数，改为实算（与带筛选同一条精确计数，
     实测热缓存日报 168ms、周报 242–375ms）。在榜行数同时是**分段分页的分界线**，必须与
     实际可翻的行数一致，不一致会让跨界页重复或漏行，而那种错误在界面上表现为「某一行出现
     两次」，极难归因。真实体量下这次计数的耗时尚未实测，若不达标再评估把"扣除黑名单后的行数"
     在导入时算定写入 `site_data_stats`
   - 带筛选的总数**精确实算、不封顶**（`analysis_crud.count_filtered`）。曾有 V1 沿用的
     `FILTERED_COUNT_CAP = 10000`，在 V2 是错的：带筛选的查询本就锁死在单一切片里，计数代价
     由切片大小决定、不随窗口增长（实测热缓存日报 168ms、周报 242–375ms），而封顶换来的
     是一个无法与精确值区分的 total——前端据此算页数，超出上限的命中行在界面上够不着
   - 每次查询都被约束在**单一站点、单一视图**内；时间维上，带筛选走单一事实切片，无筛选
     走行窗口（天 7 期 / 周 4 期）。掉榜段读的是导入时算定的 `fact_dropped`，查询侧不做
     反连接——那正是决策 1 要避开的「每翻一页重算一次」
   - 类目下拉读 `category_stats`（按日期分片的普通表，导入后只重算当期切片），不实时聚合
3. **Import**: 走 `COPY` 流式装载到 UNLOGGED 暂存表，内存有界；不再有 pandas 分块或多进程分片
   （V1 的 `BATCH_SIZE`/`MAX_WORKERS`/`FILE_SPLIT_LINES` 等配置已随 V1 一并删除）。
   导入以 `pg_try_advisory_lock` 串行化，同一时刻只跑一个。
