## Context

V2 星型模型已落地（`fact_daily`/`fact_weekly` 结构一致、按 `report_date` 月度 RANGE +
`site` LIST 二级分区；维度 `dim_keyword`/`dim_product`；`site_data_stats` 记每期行数；
`category_stats` 物化视图记类目计数）。查询层仍是 V1（查退役单表 `amazon_origin_search_data`）。
要求已写定在 add-multi-site-data-model 的 deferred-specs/site-scoped-query（9 条 Requirement）。
本设计定「怎么查」，spec 落「查成什么样」。

V1 查询的关键差异：V1 把日报与周报塞在同一行（`current_rangking_day`/`_week` 并存），V2 是两张
独立事实表、两个独立视图，语义上不再有"一行里同时有日和周"。

## Goals / Non-Goals

**Goals:**
- 以「单站点 + 单视图 + 单日期」为作用域重写分析查询，严格不越界。
- 日期选择器/分页总数走 `site_data_stats`、类目走 `category_stats`，不在 8 亿行上实时聚合。
- 日视图正确附带"截至所选日期的近 7 天走势"与"当周周排名变化"，且不改变结果集构成。
- 列表输出走阶段三 ProTable 适配层，解锁前端阶段四。
- 分期落地，每期可独立验证。

**Non-Goals:**
- 不改数据模型、分区、导入、归档（只读这些表）。
- 不引入新的聚合物化视图（复用已有 `site_data_stats`/`category_stats`）。
- 不做跨站点/跨日期对比视图（作用域刻意单一）。
- 不在本变更做前端屏（阶段四单独做，消费本变更的接口）。

## Decisions

**决策 1：作用域三元组 (site, view, date) 在服务层解析一次，贯穿所有查询。**
- `site`：未传取配置默认 `DEFAULT_SITE`；一律经 `app.site_registry.normalize_site_code` 校验，
  非法即 422，MUST NOT 静默回退。
- `view`：`DAY`|`WEEK`，决定查 `fact_daily`（日期列 `report_date`）还是 `fact_weekly`（`report_date_week`）。
- `date`：未传取该 (site, view) 在 `site_data_stats` 里的最新可用日期；传了则校验其确在
  `site_data_stats` 内（有数据且在保留窗口），否则返回明确错误。响应回显生效的 site/view/date。

**决策 2：日期选择器与分页总数只读 `site_data_stats`，不碰事实表。**
- 可选日期 = `SELECT report_date FROM site_data_stats WHERE site=? AND period_type=? ORDER BY report_date DESC`。
- 无筛选时的总数 = 该 (site, period, date) 行的 `row_count`（O(1)）。
- 有筛选时沿用 V1 策略：`LIMIT 10000` 上限内精确计数（避免筛选下扫全切片）。这条写进 spec 的
  "既有的精确或上限统计策略"。

**决策 3：主查询 = 单分区事实切片 JOIN 维度，边界由 WHERE 锁死。**
- FROM `fact_<view>` WHERE `site=?` AND `<date_col>=?`（命中单个站点子分区，配合
  `idx_*_ranking` 等索引）。
- JOIN `dim_keyword ON keyword_id`（取 keyword 文本；关键词子串搜索 `keyword ILIKE '%x%'` 走
  `dim_keyword` 的 trgm 索引）。
- LEFT JOIN `dim_product ON top1_product_id`（取 top1 的 brand/category/title/asin；对这四者的
  子串搜索走 `dim_product` trgm 索引）。top2/top3 字段结果里返回但不参与筛选（沿用 V1 只筛 top1）。
- 站点/日期 WHERE 在最外层恒定，任何筛选/排序/分页 MUST NOT 移除它——用固定的 base query 起手，
  filter 只做 AND 叠加。

**决策 4：排序字段按视图白名单，非法回落默认。**
- 允许排序：`current_ranking`、`ranking_change`、`previous_ranking`、`top1_click_share`、
  `top1_conversion_share`、`is_new`。两视图字段名相同（表结构一致）。
- **日视图明确拒绝**按"7 天走势""当周排名变化"排序（它们是附加信息、非结果集来源字段）——
  收到就忽略、回落 `current_ranking ASC`，而非报错或返回错乱结果。
- 默认排序：`current_ranking ASC`（V1 实测最热路径，对应 `idx_*_ranking`）。

**决策 5（B 期，最难）：日视图的 7 天走势与当周排名变化，按"先分页、再补附加"两步走。**
- 先执行主查询拿到本页 N 行（含各行 `keyword_id`），再对这批 `keyword_id` 批量补附加信息，
  避免在整切片上做相关子查询。
- **7 天走势**：`SELECT keyword_id, report_date, current_ranking FROM fact_daily
  WHERE site=? AND keyword_id = ANY(:ids) AND report_date BETWEEN :date-6 AND :date
  ORDER BY keyword_id, report_date`——走 `idx_fact_daily_keyword_history`（列序 site,keyword_id,date）。
  在服务层按 keyword_id 归并成每行的走势数组。历史不足 7 天就少几个点，不补齐、不报错。
- **当周排名变化**：所选日期映射到其所属周的周报日期（周报 `report_date_week` 的口径由导入侧定，
  查询侧用"该日期 ≤ report_date_week 的最近一期"或等值映射，实施期对齐导入的周日期规则），
  再 `SELECT keyword_id, ranking_change FROM fact_weekly WHERE site=? AND report_date_week=?
  AND keyword_id = ANY(:ids)`。当周周报缺失则该列为空，其余正常。
- 附加信息只读、只按 keyword_id 关联本页行，MUST NOT 改变结果集行数或构成。

**决策 6：类目下拉走 `category_stats` 物化视图，按 (site, period) 取选项与计数。**
- `SELECT category, count FROM category_stats WHERE site=? AND period_type=? ORDER BY count DESC`。
- 某站点无数据 → 空列表、正常返回。黑名单类目（Books/Grocery 等，沿用 V1 默认过滤）在此一并排除
  或标注，具体清单沿用 V1 常量。

**决策 7：分三期实现，每期可验。**
- **A 期**：作用域解析 + 日期选择器 + 主分页查询（fact+dims+筛选+排序+总数）+ ProTable 输出。
  日/周视图都通，暂不含走势/周变化/类目下拉。产出一个能用的分析接口。
- **B 期**：日视图 7 天走势 + 当周排名变化（决策 5）。
- **C 期**：类目选项（决策 6）+ 打磨（黑名单默认过滤、字段命名对齐前端）。

**决策 8：响应 DTO 按视图区分，但列表包一层 ProTable 契约。**
- 行 DTO 分 `DayRowItem`（含 trend、week_change）与 `WeekRowItem`（无这两者）。
- 列表出口用阶段三 `ProTableResponse[T]`；作用域回显（生效 site/view/date、可选日期集）放在
  另一个轻量 meta 接口或响应扩展字段里（实施期定，不破坏 ProTable 的 `{data,total,success}`）。

## Risks / Trade-offs

- **[周日期映射口径]** 当周排名变化依赖"日报日期 → 周报期次"的映射规则。风险是与导入侧写入
  `report_date_week` 的口径不一致导致错配。缓解：B 期实施前先核对导入链路的周日期定义，映射逻辑
  以导入侧为准并加针对性测试。
- **[有筛选时的总数]** `LIMIT 10000` 上限计数在极端筛选下仍是近似（沿用 V1 权衡）。缓解：无筛选
  走 `site_data_stats` 精确值，覆盖最常见路径；上限值可配置。
- **[两步查询的往返]** B 期"先分页再补附加"是两到三条 SQL。缓解：附加查询按 keyword_id 批量、走
  专用索引，页大小有限（≤ perPage），成本可控；避免了在整切片上做相关子查询的更大代价。
- **[category_stats 时效]** 物化视图只在导入后刷新（由导入链路负责）。查询侧读到的是上次刷新的
  快照，与刚导入未刷新的数据可能短暂不一致。可接受：类目集合变化慢，且导入链路应在导入末刷新。
- **[V1/V2 字段命名]** 前端阶段四要接。风险是响应字段名与 V1 不一致导致前端返工。缓解：DTO 字段名
  在本变更定稿并写进 spec 的响应约定，阶段四按此实现。
