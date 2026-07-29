## 1. 作用域解析与配置（贯穿）

- [x] 1.1 新增配置 `DEFAULT_SITE`（`config.py` + `.env.example`）：校验须属站点清单，`default_site` 属性留空时回退首个启用站点
- [x] 1.2 作用域解析（`analysis_service._resolve_site`/`_resolve_date`）：site 未传取默认、经 `normalize_site_code` 校验（非法抛 `ScopeError`→ /search 返 success:false、/scope /categories 返 422，不回退）；date 未传取最新、传了校验在 `site_data_stats` 内否则明确报错
- [x] 1.3 请求/响应 schema 重构（`search_schemas.py`）：`AnalysisSearchRequest`（site/view/date+筛选+排序+分页）、`DayRowItem`（含 trend/week_change）/`WeekRowItem`、`AnalysisSearchResponse`（ProTable 契约+回显）、`ScopeInfo`、`CategoryOption`。（避坑：字段名 `date` 遮蔽类型，日期类型统一用别名 `_Date`）

## 2. A 期：核心作用域查询（可用分析接口）

- [x] 2.1 CRUD 重写（`analysis_crud.py`）：`_base_query` = `FROM fact_<view> WHERE site=? AND <date_col>=?` 边界恒定；JOIN `dim_keyword`、LEFT JOIN `dim_product`（top1）
- [x] 2.2 筛选叠加（`_apply_filters`，AND，不突破边界）：关键词/品牌/类目/ASIN/标题子串、category_exact、排名/变化/份额区间、is_new
- [x] 2.3 排序白名单（`_apply_order` + `SORTABLE_FIELDS`）：非法字段回落 `current_ranking ASC` + keyword_id 兜底稳定分页；日视图的 trend/week_change 不在白名单故被拒
- [x] 2.4 分页总数：无筛选读 `site_data_stats.row_count`（`scope_row_count`）；有筛选 `LIMIT 10000` 上限计数（`count_filtered`，`FILTERED_COUNT_CAP`）
- [x] 2.5 日期选择器：`available_dates` 读 `site_data_stats` 倒序；`GET /api/analysis/scope` 出 ScopeInfo
- [x] 2.6 Service（`analysis_service.py`）解析作用域+组装；API（`analysis_api.py`）瘦控制器：`POST /search`、`GET /scope`、`GET /categories`，均 `require_permission(analysis:view)`；回显随响应
- [~] 2.7 切换/翻页语义：后端保证每页只含当前作用域（已验）；切换回第 1 页由前端阶段四配合

## 3. B 期：日视图附加信息（7 天走势 + 当周排名变化）

- [x] 3.1 `service.search` 日视图分支：先分页拿本页 keyword_id，再批量补附加，避免整切片相关子查询
- [x] 3.2 近 7 天走势（`crud.fetch_trends`）：`fact_daily WHERE site AND keyword_id IN ids AND report_date BETWEEN date-6 AND date`（走 `idx_fact_daily_keyword_history`），按 keyword_id 归并；历史不足不补齐（已测 `[5,3]`/`[9]`）
- [x] 3.3 当周排名变化（`crud.fetch_week_changes`）：**核实导入侧只存文件名日期、不定义周边界** → 查询侧采同 ISO 周口径（`date_trunc('week')` 相等）；先在 `site_data_stats` 解析周报期次、再等值查 `fact_weekly`（利分区裁剪）；缺失则空（已测：映射得 7、仅日报关键词得 None）
- [x] 3.4 附加信息只按 keyword_id 关联本页行，不改结果集构成（DayRowItem 逐行 set trend/week_change）

## 4. C 期：类目选项与打磨

- [x] 4.1 类目下拉：`category_options` 读 `category_stats` 物化视图（列 top_category/cnt），按 (site, period) 返回选项+计数；`GET /api/analysis/categories`（已验，US 日视图 20 个类目）
- [~] 4.2 黑名单类目默认过滤 —— **可选/推迟**：不在 site-scoped-query spec 内，是「沿用 V1」的可选项；默认隐藏类目属行为决定，待产品确认是否需要再加（届时加请求开关，默认可配）
- [x] 4.3 字段命名定稿：DayRowItem/WeekRowItem/AnalysisSearchResponse 字段已被前端阶段四消费且稳定，无字段漂移

## 5. 测试（集成，`TEST_DATABASE_URL` gate）

> 集成测试见 `test/test_site_scoped_query_integration.py`（TEST_DATABASE_URL gate，受控小样本，11 项全绿）。

- [x] 5.1 作用域边界：查 D3 不返回只在 D1 出现的关键词（test_scope_boundary_excludes_other_dates）
- [x] 5.2 日期选择器：只列有数据的日期（倒序）、未传默认最新、越界日期报错不回退
- [x] 5.3 日视图走势：按所选日期回溯（`[5,3,1]`/截至 D2 得 `[5,3]`）；历史不足不补齐；当周周报缺失/仅日报关键词 week_change 为 None
- [x] 5.4 周视图：总数取该周、行不含 trend/week_change 字段（周报独有关键词可见待补断言）
- [x] 5.5 排序白名单：非法字段 trend 回落默认 current_ranking 升序
- [x] 5.6 总数：无筛选等于 `site_data_stats`；带筛选按上限策略（test_filtered_total_reflects_filter）；周视图总数独立于日视图
- [x] 5.7 ProTable 契约形状（test_response_has_protable_contract）；失败形状 success:false 真机已验（非法站点 ZZ）

## 6. V1 查询路径退役

- [x] 6.1 `analysis_crud/service/api` 已重写，不再引用 `AmazonOriginSearchData`（V1 `analysis_model.py` 文件的删除随 amis 收尾，见前端迁移阶段七）
- [x] 6.2 更新 `CLAUDE.md`：分析查询走 V2 星型模型、作用域为「站点+视图+日期」、日期选择器/总数走 `site_data_stats`、类目走 `category_stats`
