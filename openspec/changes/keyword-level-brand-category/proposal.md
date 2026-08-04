## Why

源报表里的「点击量第 1/2/3 的品牌」与「点击量最高/第二/第 3 的分类」是**关键词级的独立
排行**——它们回答的是「搜这个词的人，点击集中在哪几个品牌、哪几个类目」，与「点击量第 N 的
商品是谁」是三份彼此独立的榜单。导入侧却把品牌#N、类目#N 当成商品#N 的属性写进了
`dim_product`（按 ASIN 唯一）。

这不是「归错了地方、挪一下就行」，而是**这两个字段在商品维度上根本没有数据来源**。实测
（US 日报 40.3 万行、20.6 万个作为第一名出现的 ASIN）：

| 字段 | 同一 ASIN **固定在槽位 1** 时的取值冲突率 |
|---|---|
| 标题 | **0.0%**（20.6 万个 ASIN 无一冲突）——它才是真正的商品属性 |
| 品牌 | **11.7%** |
| 类目 | **3.4%** |

例：`B07BGLT25K` 稳定是某个关键词的第一名商品，品牌列却先后是 `ANGEL SOFT` /
`Amazon Basics` / `Bliwov` / `Charmin`——一组卫生纸品牌，正是那个关键词的品牌排行。

后果不止于显示错误：`dim_product` 写入用 `DISTINCT ON (asin)` 且无次级排序，冲突值中**任选
一个**胜出，所以品牌与类目筛选匹配的是一个不确定的值；`category_stats` 物化视图也建在
`dim_product.category` 上，类目下拉的选项与计数同样失真。

`show-top3-products` 已因此刻意不展示按名次的品牌与类目——那个变更把「等数据模型修好」写进了
规格。本变更就是那个前提。

**影响范围：日报与周报两条路径。** 两张事实表结构一致，导入、查询、界面对两者共用同一套代码。

## What Changes

- 新增品牌维度与类目维度（`dim_brand` / `dim_category`），事实表增加各自前三名次的引用列，
  与既有的三个商品槽位并列。名次由列位置承载，与商品槽位同一套语义。
- **BREAKING**：删除 `dim_product.brand` 与 `dim_product.category` 两列及其索引。它们没有数据
  来源，保留只会让后来者以为「商品有品牌」。`dim_product` 收敛为 ASIN + 标题。
- 导入链路补齐两个新维度与事实表的六个新引用列。
- 品牌与类目筛选改为**前三任一命中**：`品牌 = Anker` 意为「这个词的前三品牌里有 Anker」。
  展示口径与筛选口径由此一致，`show-top3-products` 里那条「只匹配点击量第一名」的界面标注
  随之移除——它存在的前提消失了。
- 结果表格加入「点击前三品牌」与「点击前三类目」两列，按名次竖排，与商品卡牌的名次对齐。
  导出同步增加两组按名次的列。
- `category_stats` 物化视图改按关键词级类目重建；类目下拉的计数含义变为「前三类目里包含该
  类目的关键词数」。
- 提供按批次重导的运维脚本：历史数据没有这六个字段，需借 `import_batch.object_key` 从 OSS
  重导。不重导会出现「同一个筛选在不同日期上行为不同」这种最难解释的不一致。

## Capabilities

### New Capabilities

无。本变更修正既有能力的数据归属与筛选口径，不引入新的能力域。

### Modified Capabilities

- `multi-site-data-model`: 品牌与类目从商品维度移出，成为关键词级的事实字段；商品维度收敛为
  ASIN + 标题。
- `site-scoped-query`: 行字段增加前三品牌与前三类目；品牌与类目筛选由「匹配第一名商品的属性」
  改为「前三任一命中」；导出同步扩列。
- `analysis-screen-layout`: 结果表格增加按名次展示的品牌与类目列；移除「展示口径宽于筛选口径
  时在界面标注」这一要求——两者已对齐，标注反而成了噪音。

## Impact

**数据库（需 Alembic 迁移，事实表是分区表，迁移必须手写）**

- 新增 `dim_brand`、`dim_category` 两张维度表。
- `fact_daily` / `fact_weekly` 各增加 6 列引用（品牌 3 + 类目 3）。按基数选类型：品牌
  13.5 万/站/天（`integer`），类目 104（`smallint`）。合计约 18 字节/行，相对现有约 140 字节
  的行宽约 +13%；三个月保留窗口下约 2 亿行，增量约 3.6 GB。
- 删除 `dim_product.brand` / `.category` 两列及其两个 trgm 索引。
- `category_stats` 物化视图重建。

**后端**

- `app/table/analysis/dimension_model.py`、`fact_model.py`：新维度表与新列。
- `app/table/upload/importer.py`：`_upsert_dimensions` 补两个维度，`_load_facts` 补六列。
- `app/table/analysis/analysis_crud.py`：筛选改为前三任一命中；行数据与导出补字段。
- `app/table/search/search_schemas.py`：请求/响应字段。
- `scripts/`：按批次重导的运维脚本。

**前端**

- 结果表格两列新增、过滤区的口径标注移除、`AnalysisRow` 扩字段。

**已知风险**

「前三任一命中」是三列 OR，现有索引服务不了。索引方案（三个独立索引走 BitmapOr，还是在
`ARRAY[b1,b2,b3]` 表达式上建 GIN）**必须在真库上用 `EXPLAIN (ANALYZE, BUFFERS)` 实测后再定**，
不得凭直觉选——V1 有过投机建索引、实测 0 命中的先例。
