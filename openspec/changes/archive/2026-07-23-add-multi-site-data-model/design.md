## Context

KeywordIQ V1 已完成使命并退役，V2 是完整重建。本变更是重建的第一步——**冻结数据模型**，后续的导入、查询、界面、归档四个变更都建立在它之上。

V1 现状与审计结论（作为设计依据保留）：

- `analysis.amazon_origin_search_data` 是一张约 32 列的宽表，日报与周报共存于同一行，靠 `ON CONFLICT (keyword) DO UPDATE` 去重，同时缺失站点维度与时间维度。
- **索引已膨胀到 24 个**（最新生产结构导出，早期为 16 个）。新增的 8 个（`idx_date_ranking_sort` / `idx_date_ranking_nonzero` / `idx_optimized_count` / `idx_brand_keyword_filter` / `idx_category_notnull` / `idx_top_category_with_id` / `idx_shares_range` / `idx_amazon_origin_ranking_day`）是事后针对慢查询打的补丁，揭示了真实热点：**"日期 + 排名"复合排序**与**默认品牌过滤的 count**。这两点恰好验证了 V2 事实表 `(site, report_date, current_ranking)` 索引的形状。
- 但这 24 个里问题依旧：`idx_optimized_count` 与 `idx_brand_keyword_filter` **定义逐字节相同**（纯重复）；`idx_date_ranking_sort` 被带过滤的 `idx_date_ranking_nonzero` 覆盖；而 keyword/brand/category/asin 的 6 个文本 btree/tsvector 索引对 `ILIKE '%x%'` 子串搜索**物理上无法使用**，title 搜索则从头到尾无索引。这正是 V2 不照搬、改用维度表 pg_trgm 的依据（决策 5b、6）。
- **90% 的行永远不可见**：`csv_processor.py:469` 让周报独有的关键词 `current_rangking_day = 0`；`analysis_crud.py:163` 对所有查询无条件施加 `current_rangking_day != 0`。
- **18 个共享列有两个写者**：日报与周报的 UPSERT 都写 Top 商品字段，最后导入者胜出。
- **单行约 650 字节，其中约 390 字节是三个商品标题**，同一热门 ASIN 的标题在数千个关键词下、连续 365 天被重复存储。
- V1 无任何 Alembic 迁移基线；`docs/analysis.sql` 已与生产库不一致（dump 中 `idx_keyword` 为非唯一索引，而代码依赖 `ON CONFLICT (keyword)`），不能作为结构依据。

已确认的业务约束：
- 一期 3 站（US/GB/DE），需可扩到 10+；**只需切站独立查看，不需要跨站对比**。
- 浏览模型为**站点 → 视图（日/周）→ 日期**。日视图附带最近 7 天走势与当周排名变化；周视图看该周周报全量，含未出现在日报中的关键词。
- **热窗口 12 个月**，更早的数据归档到大数据平台供数据挖掘与 AI 训练。
- V2 全新导入，不迁移 V1 数据。

数据规模：日报约 30 万行/站/天，周报约 300 万行/站/周。12 个月 × 3 站约 **8 亿行**。

## Goals / Non-Goals

**Goals:**
- 站点、时间、周期三个维度都成为数据模型中的一等公民。
- 在 12 个月保留窗口下把存储控制在可承受范围。
- 数据写入后不可变，使导入可以是纯追加操作。
- 结构上支持按月归档（`DETACH` → 导出 → `DROP`）。
- 站点扩到 10+ 不需要修改表结构。
- 建立 Alembic 基线，让后续结构变更可版本化。

**Non-Goals（均为后续变更或明确排除）:**
- 导入链路的实现（变更 2）。
- 查询链路的实现，包括双视图、日期选择器、7 天走势的计算（变更 3）。
- 界面（变更 4）。
- 归档管道的实现（变更 5）——本变更只保证结构就绪。
- 月度/季度报告周期（明确排除）。
- 跨站点对比同一关键词（二期）。
- V1 数据迁移与向后兼容。

## Decisions

### 决策 1：星型模型 —— 事实表 + 维度表

```
dim_keyword  (keyword_id, site, keyword)                 UNIQUE(site, keyword)
dim_product  (product_id, asin, title, brand, category)  UNIQUE(asin)

fact_daily   (site, report_date, keyword_id,
              current_ranking, previous_ranking, ranking_change, is_new,
              top1_product_id, top1_click_share, top1_conversion_share,
              top2_..., top3_...)                        UNIQUE(site, report_date, keyword_id)

fact_weekly  (site, report_date_week, keyword_id, ...同上)
                                                         UNIQUE(site, report_date_week, keyword_id)
```

理由：数据中存在两类巨量重复——同一个商品标题被数千个关键词、连续 365 天重复存储；同一个关键词也重复 365 次。

| 方案 | 事实表单行 | 8 亿行合计（含索引） |
|---|---|---|
| V1 宽表 | ~650 B | ~750 GB |
| **星型模型** | **~140 B** | **~200 GB** |

两个额外收益：
1. **关键词搜索的 GIN 索引从 8 亿行降到约 1500 万行**（建在 `dim_keyword` 上），体积从约 60 GB 降到 1 GB 以内。这是单项最大的节省。
2. **品牌与类目筛选落到几百万行的 `dim_product` 上**，而不是 8 亿行的事实表。

代价：查询需多 2–4 个 JOIN。维度表小到可常驻内存，且关联应发生在分页之后——这是对变更 3 的约束，见文末"对下游变更的约束"。

**SCD 语义**：`dim_product` 采用 Type 1（最新值覆盖）。若某 ASIN 的标题或类目变化，历史事实行会关联到新值，无法还原当时的展示内容。对当前分析场景可接受；但归档给 AI 训练时必须把维度值物化进导出文件，否则语料失真（对变更 5 的约束）。

**`site` 同时存在于 `dim_keyword` 与事实表**，属于刻意冗余：事实表需要它做分区裁剪与过滤，不能每次都靠 JOIN 才能确定站点。

### 决策 2：事实表按 `report_date` 月度 RANGE 分区，再按 `site` 二级 LIST 分区

- **一级 RANGE(report_date) 按月**：日期选择器每次只查一天，月度分区把扫描压到 1/12。归档时 `DETACH` 一整月，无需 `DELETE`。
- **二级 LIST(site)**：站点数据物理隔离（业务明确要求"分区的数据是绝对隔离的"），扫描再压到 1/3。

分区数量：12 个月 × 3 站 = 36 个叶子分区/事实表，两张表共 72 个。PostgreSQL 在这个量级下规划开销可忽略（问题通常出现在数千个分区时）。

不按天分区的原因：365 × 3 = 1095 个叶子分区，规划时间开始变得可观，而收益已被分区内 `(site, report_date, ...)` 的索引覆盖。

PostgreSQL 要求分区表的唯一约束必须包含分区键，唯一键 `(site, report_date, keyword_id)` 天然满足。

**必须自动化**：分区缺失会让导入直接报错。本变更需提供一个幂等的"确保分区存在"函数（给定站点与日期，创建缺失的月份分区与站点子分区），不能依赖人工预建——这是分区方案最常见的运维事故来源，属于地基责任而非导入层责任。

### 决策 3：事实数据不可变

事实行的排名、上期排名、排名变化、是否新词等字段在写入时一次性算定，此后不再修改。数据模型**不提供任何原地更新路径**。

这是一个数据模型层面的决策，而非导入实现细节，因为它同时决定了：
- 表上不需要为频繁更新预留 `fillfactor`；
- 不需要为死元组规划 autovacuum 策略（追加模型下死元组接近零）；
- 归档成立的前提——已归档的月份不会有回写需求；
- 派生字段（走势）不需要冗余存储，可以从历史行计算。

**唯一的例外是同一期重复导入**（纠错场景）：先移除该 `(site, period, report_date)` 切片再写入。这会产生死元组，但属于罕见操作。

### 决策 4：不设冗余的派生字段

V1 的 `ranking_trend_day` JSONB 之所以存在，是因为当时没有历史行。时序化之后它是纯冗余，本变更不再设置该字段。7 天走势由查询层从历史事实行计算（对变更 3 的约束）。

连带收益：V1 中 `_fetch_existing_trends` 缺少站点条件导致的跨站趋势污染风险，随该字段一并消失。

### 决策 5：`site` 的列类型——`varchar(8)`，不用 PG enum，不加外键

- **不用 PG enum**：新增站点需 `ALTER TYPE ... ADD VALUE`，违反"新增站点不修改表结构"。
- **不加外键指向 `sites`**：批量灌入时每行一次 FK 检查是纯粹的导入税。
- **不加 `CHECK (site IN (...))`**：同样属于新站点要改约束。
- **结论**：`site varchar(8) NOT NULL`，合法性由应用层单一事实来源保证（Python 枚举 + 配置）。

代价：绕过应用层直接写库可写入非法站点码。补偿：`analysis.sites` 参考表 + `SELECT DISTINCT site` 巡检纳入健康检查。

**站点清单属于数据模型的一部分**，因为它决定分区的创建，所以纳入本变更而非导入变更。

### 决策 5b：搜索字段全部在维度表，用 pg_trgm 子串索引

界面对 keyword / title / brand / category / asin 五个字段都做子串搜索（`ILIKE '%x%'`，
沿用 V1 行为）。星型模型下这五个字段全部落在两张维度表：keyword 在 `dim_keyword`，
其余四个在 `dim_product`。事实表**不携带任何可搜索的文本字段**。

这带来一个必须正视的后果：**每一次带搜索条件的查询都是"维度过滤 → 事实 join"**，
而结果集的排序与分页在事实表上——过滤谓词与排序键分处两张表，这是雪花模型的经典难点。

为什么仍然选它，而不是把文本冗余回事实表：

1. **搜索索引的规模。** 子串搜索需要 pg_trgm 三元组 GIN。建在维度表上：`dim_keyword`
   约 500 万行/站、`dim_product` 数百万行，索引小。若把文本冗余回事实表再建 trigram，
   那是 **8 亿行的 trigram GIN，几十 GB**，并且拖慢每月 6600 万行的热写路径。维度方案
   在两个方向上都更省。
2. **不比 V1 差，通常更好。** V1 的 `ILIKE '%x%'` 在旧表上**没有可用索引**（`idx_keyword`
   是普通 btree，对 `%x%` 无效），每次搜索都是全表顺序扫描。V2 至少有 trigram 索引兜底。
3. **单分区作用域封顶。** 无论怎么搜，事实侧永远被 `(site, report_date)` 限定在一天约
   30 万行（周报一周约 300 万行），join 的规模有天花板。

**索引类型的坑（我在初版建错过一次）**：子串 `ILIKE` 必须用 `gin_trgm_ops` 三元组索引，
**不能用 `to_tsvector` 全文 GIN**——全文索引对子串匹配无效，用错会让搜索静默退化成全表
扫描，正是 V1 的病根。本变更已把五个字段全部建为 pg_trgm 索引，并 `CREATE EXTENSION pg_trgm`。

**留给变更 3 的硬约束与风险**：
- 带搜索的查询必须让 planner 先在维度上用 trigram 过滤、再 join 事实，或反之取决于选择性。
  两种计划都要在 `EXPLAIN` 里确认，不能假设。
- **真正的病态场景**：低选择性子串（如 `%a%`，命中几乎所有词）+ 按排名排序 + 深翻页。
  此时维度过滤与事实排序都不选择性，两边都是大集合。这是唯一需要重点实测的模式。
  退路（若实测确实病态）：对该字段做**定向冗余**——只把这一个字段回写事实表，而不是
  推翻整个星型模型；或对高频搜索词加缓存。
- title 搜索目前只覆盖 top1（展示的"Top 商品"即 top1）。是否需要覆盖 top2/top3 尚未确定，
  见 Open Questions。

### 决策 6：索引先裁剪，全部以 `(site, report_date)` 为前导

V2 的事实表索引集**由 V1 生产库 `pg_stat_user_indexes` 的真实 `idx_scan` 定型**，不是照搬也不是猜测。V1 的 24 个索引实测暴露了三类事实：

- **热点**（万级扫描）：日期+排名复合（`idx_date_ranking_nonzero` 81870）、新词筛选（`idx_amazon_new_day` 59178）、报告日期（`report_date_week` 87388、`report_date_day` 15662）、排名变化（`ranking_change_day` 4543）。
- **投机废索引**（0 次扫描）：`idx_shares_range`、`idx_amazon_click_share_range`、`idx_amazon_conversion_share_range`、`idx_category_notnull`、`idx_amazon_top_brand_not_null`，以及全文 `idx_keyword_text`。有人加了一批"范围/部分"优化索引，**从未被命中**。
- **近乎死索引**：文本搜索 btree（asin 3、category 28、brand 63）——印证子串 `ILIKE` 用不上它们。而 `idx_keyword`（35M）与 `unique_keyword`（262M）的高扫描量来自**导入路径的等值查询**（`ON CONFLICT` 与 `keyword = ANY`），不是搜索框；这条路径在 V2 由 `dim_keyword` 的唯一约束覆盖。

据此确定的 V2 事实表索引（见 `fact_model._fact_indexes`）：

1. 以 `(site, report_date, ...)` 为前导列，建在分区级——每个叶子分区各自持有索引。
2. `_ranking`（日期+排名）对应 V1 最热路径。
3. `_new`（`WHERE is_new` 部分索引）对应新词筛选——V1 实测 5.9 万次，是真高频，只索引少数新词故体积小。
4. `_top1_product` 服务品牌/类目筛选（V1 `idx_optimized_count` 2736、`idx_top_category_with_id` 1908）。
5. 份额索引保留全量排序版，**不建任何"范围"部分索引**——V1 同类实测 0 命中。
6. **两张事实表各一套完整索引**：日视图与周视图各自独立驱动查询，`fact_weekly` 不是附属表。
7. 搜索索引（pg_trgm 子串）只在维度表，事实表不建——详见决策 5b。

### 决策 7：类目物化视图按 `(site, period_type, top_category)`

类目分布在两个视图下不同（日视图约 30 万行、周视图约 300 万行），不能共用一份。实现为单张物化视图，由两张事实表关联 `dim_product` 的聚合结果 `UNION ALL` 而成，带 `period_type` 列区分。

在 `(site, period_type, top_category)` 上建唯一索引以支持 `REFRESH ... CONCURRENTLY`（刷新时机由导入变更负责）。

### 决策 8：`site_data_stats` 承担总数与可选日期

全表 `reltuples` 估算在多站点多日期下毫无意义。`analysis.site_data_stats` 记录每个 `(site, period_type, report_date)` 的行数与更新时间，同时服务两个用途：分页总数 O(1) 读取，以及日期选择器的"哪些日期有数据"。

### 决策 9：Alembic 基线从零建立

V2 不迁移 V1 数据，基线问题因而变简单：**不需要描述旧结构、不需要 `alembic stamp`**，直接生成一份"从零创建全部新表"的迁移。

配置要求：`version_table_schema='analysis'`、`include_schemas=True`、`target_metadata=Base.metadata`，连接串复用 `config.py`。所有迁移显式带 `schema='analysis'`，不依赖 `search_path`。

### 决策 10：V2 使用新的 RDS 实例

V1 的 2C4G 无法承载 8 亿行。相比原地升配，新建实例的优势：
- V1 实例保留，作为回滚兜底与历史数据来源；
- 升配窗口不影响仍在服务的 V1；
- V2 可以从干净的参数配置开始，不继承 V1 的历史设置。

## 容量与配置

**历史 CSV 不存在，数据从 V2 上线当天开始积累。** 因此容量不是一次性的 8 亿行，而是一条线性增长曲线。

三站合计约 **6600 万行/月**（日报 2700 万 + 周报 3900 万），按单行约 140 字节估算，**含索引约 16 GB/月**：

| 时点 | 累计行数 | 含索引体积 |
|---|---|---|
| 第 3 个月 | 约 2 亿 | ~50 GB |
| 第 6 个月 | 约 4 亿 | ~100 GB |
| 第 12 个月（热窗口满） | 约 8 亿 | ~200 GB |

满窗后进入稳态：每月新增约 16 GB，同时归档移出约 16 GB。

**分阶段选型**，而不是一次性按终点下单：

| 阶段 | 实例 | 存储 |
|---|---|---|
| 上线 ~ 第 4 个月 | 4 vCPU / 16 GB | ESSD PL1，200 GB |
| 第 4 ~ 第 12 个月 | 8 vCPU / 32 GB | ESSD PL2，500 GB |
| 满窗稳态 | 8 vCPU / 32 GB | ESSD PL2，1 TB |

阿里云 RDS 的存储可在线扩容，规格变更需要一次短暂重启。整个第一年只需两次扩容窗口，换来的是前期成本降到三分之一左右。

**扩容触发条件**（写死，避免临时拍脑袋）：磁盘使用率超过 70%，或缓存命中率（`pg_stat_database` 的 `blks_hit / (blks_hit + blks_read)`）持续低于 95%。

关键参数（按最终的 32 GB 规格给出，16 GB 阶段按比例减半）：

关键参数：

```
shared_buffers        = 8GB        # RAM × 25%
effective_cache_size  = 24GB       # RAM × 75%
work_mem              = 32MB       # ×200 并发，别调大
maintenance_work_mem  = 2GB        # 建索引与归档
random_page_cost      = 1.1        # ESSD，不要用默认 4
max_wal_size          = 16GB       # 批量导入避免频繁 checkpoint
checkpoint_timeout    = 15min
```

**上述全部基于按列宽推算的估算，误差可达 ±30%——在这个量级上就是几十 GB 的差别。下单前必须先用 V1 生产表实测单行字节数**（见 tasks 1.1）。

## 对下游变更的约束

本变更冻结的模型对后续四个变更施加以下约束，记录在此以免在拆分中丢失：

**变更 2（导入）**
- 只能追加，不得原地更新事实行；派生字段在写入时算定。
- 必须先补齐维度、再写事实。
- 写入前必须调用本变更提供的"确保分区存在"函数。
- 上一期关联只能取同站点同周期的数据，不得跨站点或跨周期继承派生值。
- 同一期重复导入需先移除该切片再写入。

**变更 3（查询）**
- 维度关联必须发生在分页之后，只对当前页约 50 行进行。这是星型模型可行性的前提，必须用 `EXPLAIN` 验证计划不是"先关联再分页"。
- 7 天走势从 `fact_daily` 历史行计算，且**基于所选日期回溯**，而非最新日期。
- 总数与可选日期读 `site_data_stats`，不得用 `reltuples`。
- 排序字段限定在当前视图事实表自己的列上；走势与当周变化是只读展示列。

**变更 5（归档）—— 目标平台已定：阿里云 OSS + MaxCompute**
- 归档粒度与分区边界一致（按月）：`DETACH` 一个月分区 → 导出 Parquet 到 OSS → MaxCompute 消费。
- `DROP` 前先 `VACUUM FREEZE`，避免事务号回卷时扫全表。
- 因 `dim_product` 是 SCD Type 1，导出时必须把维度值物化进文件（否则历史语料失真）。
- 原始 CSV 与结构化 Parquet 均归档到 OSS，并记录 schema 版本；原始 CSV 从第一天起就存。

## Risks / Trade-offs

- **[星型模型把成本转移到了查询层]** → 本变更用存储换来的收益，需要变更 3 用正确的查询写法兑现。若关联发生在分页之前，性能会比 V1 宽表更差。这个风险在地基阶段无法验证，必须在变更 3 中用真实数据量 `EXPLAIN` 把关。

- **[分区管理未自动化会导致导入失败]** → 已作为本变更的交付项（决策 2），而非留给导入变更。

- **[容量估算有 ±30% 误差]** → 在 200 GB 量级上是几十 GB 的差别，直接影响实例选型。必须先实测再下单。

- **[`dim_product` 的 SCD Type 1 丢失时点准确性]** → 当前分析场景可接受，但归档给 AI 训练时必须物化维度值，否则语料失真。已写入对变更 5 的约束。

- **[星型模型的查询计划要在真实规模下才现形]** → "维度关联发生在分页之后"这个假设，在小数据上执行计划可能选对、大数据上却翻盘。但这**不构成上线阻塞**：结构正确性用小样本即可验证（tasks 第 7 组），而查询性能的真实规模验证属于变更 3（查询链路）的职责，且那时数据也在自然增长。真要提前压测，`scripts/generate_synthetic_data.py` 是可选工具，不是必经步骤——不必为了"验证"去人为制造一年后才会有的数据量。

- **[原始 CSV 一旦丢失不可再生]** → 归档管道（变更 5）因为热窗口一年内不会满，优先级可以后置；但**原始 CSV 的归档应该从第一天就做**，与分区归档解耦。它成本极低（处理完上传 OSS 即可），而第一年的原始报表若未留存就永久丢失——对 AI 训练语料而言这是不可逆的损失。建议在变更 2（导入）中顺手落地，不必等变更 5。

- **[未加数据库层站点约束]** → 接受，补偿见决策 5。

- **[GIN 索引对非英语站点分词不准]** → 一期接受，记入 Open Questions。

## 上线方案

本变更只建结构，不含数据与业务逻辑，因此上线本身风险极低——真正的风险在后续变更。

1. 用 V1 生产表实测单行字节数，校准容量估算。
2. 采集 V1 库 `pg_stat_user_indexes`，产出索引裁剪清单并 review 通过。
3. 确定 V2 实例规格并开通新的 RDS 实例，按推荐参数配置。
4. 定死站点代码清单与书写标准（ISO alpha-2，明确 `GB` 而非 `UK`）。
5. 在本地空库验证 Alembic 迁移可 `upgrade` / `downgrade`。
6. 在 V2 实例执行建表迁移，创建首批月度分区。
7. 用小样本数据验证分区裁剪、"确保分区存在"函数的幂等性与跨月自动建分区。

V1 实例在整个 V2 建设期间保持不动，作为回滚兜底与历史数据来源。

## Open Questions

（本变更范围内的问题均已解决。以下一条属未来功能，非本次范围。）

1. 当前搜索是子串 `ILIKE`，pg_trgm 对语言无依赖、各站点通用。**若未来要做真正的全文检索**（分词、相关性排序），才需要考虑按站点的 `regconfig`（如 DE 用 `german`）——那是新功能，不在本次范围。

## 已解决的决策（业务确认）

- **12 个月历史 CSV 不存在**，数据从 V2 上线当天开始积累。→ 实例分阶段选型（见容量与配置）；归档管道紧迫性推迟约一年，但原始 CSV 归档立即开始（见 Risks）。
- **title 搜索只覆盖 top1，不加 top2/top3。** V1 有 top1 标题搜索（`top_product_title` 的 ILIKE，但无索引、裸扫），从无 top2/top3。V2 保留 top1（`dim_product.title` 的 trigram 让它真正走索引），不新增 top2/top3。当前事实表只索引 `top1_product_id`，已符合此决定，无需改动。
- **多站点报告日期不需要对齐。** 各站点独立，日期选择器按站点分别展示可选日期（`site_data_stats` 已按 `(site, period_type, report_date)` 支持）。无跨站点对齐逻辑。
- **归档目标平台：阿里云 OSS + MaxCompute。** 变更 5 的导出落 OSS、由 MaxCompute 消费；原始 CSV 与结构化 Parquet 均归档，导出时物化 `dim_product` 维度值（决策 1 的 SCD Type 1 要求）。
