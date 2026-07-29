## Why

KeywordIQ V1 已完成使命并退役。V2 是一次完整重建，本变更是重建的**第一步：冻结数据模型**。后续的导入链路、查询链路、界面与归档管道都建立在它之上，因此必须先定死。

V1 的 `analysis.amazon_origin_search_data` 是一张把多个维度全部焊死的宽表，四个结构性缺陷共同决定了它无法演进：

**一、没有站点维度。** 唯一键是 `keyword`，`ON CONFLICT (keyword) DO UPDATE` 会让 GB 报表**静默覆盖** US 的排名与 Top 商品——不报错、不可回溯。业务需要 US/GB/DE 三站起步、可扩到 10+。

**二、没有时间维度。** 每天的导入覆盖同一行，历史只剩一个 7 天趋势 JSONB。业务要求的**按任意已导入日期回看当天数据**在这个模型上无法实现——数据根本不存在。

**三、日报与周报共用一行。** `csv_processor.py:469` 让周报独有的关键词 `current_rangking_day = 0`，`analysis_crud.py:163` 又对所有查询无条件施加 `current_rangking_day != 0`，导致周报独有的约 270 万行（占 90%）被存储、被 24 个索引索引、每周被 UPSERT，却从未展示。两条 UPSERT 还都写同一批 Top 商品列，展示内容取决于谁最后导入。

**四、宽表在 12 个月保留窗口下不可持续。** 单行约 650 字节，其中约 390 字节是三个商品标题——同一个热门 ASIN 的标题会在数千个关键词下、连续 365 天被重复存储。12 个月 × 3 站约 8 亿行，宽表约需 **750 GB**，规范化为星型模型后约 **200 GB**。

## What Changes

本变更**只交付数据结构**：表定义、约束、分区、索引、ORM 模型与 Alembic 基线。不含任何导入或查询的业务逻辑。

- 建立星型模型：

  | 表 | 作用 | 规模（3 站 / 12 个月） |
  |---|---|---|
  | `dim_keyword` | 关键词维度，`(site, keyword)` 唯一 | 约 1500 万行 |
  | `dim_product` | 商品维度（ASIN → 标题、品牌、类目） | 数百万行 |
  | `fact_daily` | 日报事实，`(site, report_date, keyword_id)` 唯一 | 约 3.3 亿行 |
  | `fact_weekly` | 周报事实，`(site, report_date_week, keyword_id)` 唯一 | 约 4.7 亿行 |

- 两张事实表按 `report_date` 做**月度 RANGE 分区**，再按 `site` 做二级 LIST 分区。
- 事实数据设计为**写入后不可变**（追加模型），不设计任何原地更新路径。
- 不设 `ranking_trend_day` 之类的冗余派生字段——7 天走势由历史事实行计算，属于查询层职责。
- 索引先按 V1 的实际使用情况裁剪再建，不做原样复制（V1 已膨胀到 24 个索引，含死索引与重复）。
- 新增 `analysis.sites`（站点参考表）与 `analysis.site_data_stats`（按站点/周期/日期的行数与最新期次）。
- 新增按 `(site, period_type, top_category)` 的类目物化视图。
- `import_batch_records` 重建，含站点、周期类型与报告日期。
- 提供幂等的"确保分区存在"函数——分区缺失会直接导致导入失败，这是分区方案最容易踩的坑，必须在地基里解决。
- 引入 Alembic 基线（V1 无任何迁移基线，`docs/analysis.sql` 已与生产库不一致）。
- 站点清单作为单一事实来源纳入配置——它决定分区的创建，属于数据模型的一部分。
- 结构上保证按月归档就绪（`DETACH` → 导出 → `DROP`）。**归档管道本身不在本变更范围内。**

## Capabilities

### New Capabilities
- `multi-site-data-model`: 数据模型的三个维度——站点、时间、周期，以及星型模型的事实表/维度表划分、唯一键语义、数据不可变性、分区与归档就绪性、站点新增时的扩展方式。

### Modified Capabilities
（无。`openspec/specs/` 当前为空，本变更是首批规格。）

## V2 的后续变更

本变更是地基。其余工作按依赖顺序拆为独立变更：

| # | 变更 | 依赖 |
|---|---|---|
| 2 | 导入链路重写：`COPY` → 维度补齐 → 事实追加、分区自动创建、站点校验 | 本变更 |
| 3 | 查询链路重写：站点/视图/日期三级作用域、日周双视图、7 天走势 | 本变更 |
| 4 | 界面重写：三级选择器、双视图列定义、上传站点选择 | 2、3 |
| 5 | 归档管道：Parquet/OSS、原始 CSV 归档、schema 版本、大数据平台对接 | 本变更 |

变更 2 与 3 的规格草稿已写好，暂存于本变更目录的 `deferred-specs/`，创建那两个变更时直接取用。

## Impact

**数据库**
- 新建：`dim_keyword`、`dim_product`、`fact_daily`（分区）、`fact_weekly`（分区）、`sites`、`site_data_stats`、`import_batch_records`、类目物化视图。
- V1 的 `amazon_origin_search_data` 及其 24 个索引、视图 `my_category_stats` 随 V1 退役废弃，不做数据迁移。
- 新增 Alembic 基线与建表迁移，全部显式指定 `analysis` schema。

**代码**
- `app/table/analysis/analysis_model.py`：重建为四个模型（两事实两维度）。
- `app/table/upload/import_model.py`：批次记录模型重建。
- `config.py` / `.env.example`：站点清单与热窗口配置。
- 新增 Alembic 目录与迁移脚本。
- **不改动**导入与查询的业务代码——那属于变更 2 与 3。

**基础设施**
- 历史 CSV 不存在，数据从 V2 上线当天开始积累，约 6600 万行/月（含索引约 16 GB/月）。12 个月满窗后约 8 亿行、约 200 GB。
- 因此**实例分阶段选型**：上线时 4 vCPU / 16 GB + 200 GB 存储即可，第 4 个月前后扩到 8 vCPU / 32 GB。整个第一年两次扩容窗口。
- V2 使用**新的 RDS 实例**，V1 实例保留作为回滚兜底。
- 归档管道（变更 5）因热窗口一年内不会满而可后置，但**原始 CSV 的归档应从第一天开始**——第一年的报表若未留存即永久丢失，对 AI 训练语料是不可逆的损失。建议在变更 2 中顺手落地。
