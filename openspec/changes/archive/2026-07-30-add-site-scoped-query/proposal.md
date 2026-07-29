## Why

V2 星型模型（`fact_daily`/`fact_weekly` + 维度表 + `site_data_stats` + `category_stats`
物化视图）已落地，但查询层仍是 V1——`analysis_crud/service/api` 查的是已退役的
`amazon_origin_search_data` 单表。这使得核心产品功能（分析查询屏）无法运作，也是前端迁移
阶段四被阻塞的唯一原因。V2 是多站点、按日/周分区的时序模型，查询语义与 V1 的"日周混在一行"
根本不同，必须重写。

## What Changes

- **BREAKING**：`app/table/analysis/` 的 crud/service/api 从查 V1 单表重写为查 V2 星型模型；
  V1 的 `amazon_origin_search_data` 查询路径退役。
- **浏览维度改为「站点 → 视图（日/周）→ 日期」**：任一时刻只查一个站点、一个视图、一个日期的
  事实切片；日报查 `fact_daily`、周报查 `fact_weekly`，互不混合。
- **日期选择器与分页总数走 `site_data_stats`**：只列出有数据且在保留窗口内的日期、默认最新、
  响应回显生效日期；分页总数按作用域 O(1) 读取，不在 8 亿行上实时聚合。
- **主查询关联维度**：事实表 JOIN `dim_keyword`（关键词文本 + pg_trgm 子串搜索）LEFT JOIN
  `dim_product`（top1 → 品牌/类目/标题/ASIN 的子串搜索）。筛选/排序/分页 MUST NOT 突破单站点单日期边界。
- **日视图附加两项只读信息**：截至所选日期的最近 7 天排名走势、当前日期所属那一周的周排名变化；
  附加信息不改变结果集构成。周视图为该周周报全量（含未在任何日报出现的关键词）。
- **排序字段按视图白名单**：日视图不允许按走势/周变化排序；非法排序字段回落默认（当期排名升序）。
- **类目下拉走 `category_stats` 物化视图**：按站点、视图计算选项与计数。
- **默认站点安全**：未传站点用配置默认（须属站点清单），非法站点返回校验错误、MUST NOT 静默回退。
- **列表响应走阶段三的 ProTable 适配层** `{data,total,success}`（前端迁移 `admin-spa` 契约）。

## Capabilities

### New Capabilities
- `site-scoped-query`: 以「单一站点 + 单一视图（日/周）+ 单一日期」为作用域的分析查询能力——
  日期选择器、边界严格的分页查询、日视图的 7 天走势与当周排名变化、按视图的排序白名单、
  类目筛选选项与作用域内总数统计。

### Modified Capabilities
<!-- 无。本变更新增 site-scoped-query 能力（其要求此前作为 add-multi-site-data-model 的
     deferred-specs 保留），不修改 multi-site-data-model 的既有要求（后者只描述数据模型/分区/
     归档，不含查询语义）。 -->
- （无。）

## Impact

- **重写**：`app/table/analysis/analysis_crud.py`、`analysis_service.py`、`analysis_api.py`
  （改查 V2 星型模型）；`app/table/search/search_schemas.py`（请求/响应按日/周视图重构）。
- **退役**：`analysis_model.py` 的 V1 `AmazonOriginSearchData` 查询路径（模型文件的清理随 amis 收尾）。
- **依赖**：读取已存在的 `fact_daily`/`fact_weekly`/`dim_keyword`/`dim_product`/`site_data_stats`/
  `category_stats`；站点合法性用 `app.site_registry`；默认站点新增配置项（`config.py` + `.env.example`）。
- **前端**：解锁前端迁移阶段四（分析查询屏 ProTable 复刻）；列表接口输出对齐 ProTable 契约。
- **性能**：日期选择器/总数走 `site_data_stats`、类目走 `category_stats` 物化视图、7 天走势走
  `idx_*_keyword_history` 索引；查询严格限定单分区切片，避免跨分区扫描。
