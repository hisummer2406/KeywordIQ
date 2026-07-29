## 1. 作用域解析与配置（贯穿）

- [ ] 1.1 新增配置 `DEFAULT_SITE`（`config.py` + `.env.example`，默认值须属站点清单）
- [ ] 1.2 作用域解析：`(site, view, date)` 三元组解析函数——site 未传取 `DEFAULT_SITE`、一律经 `site_registry.normalize_site_code` 校验（非法 422，不回退）；view ∈ {DAY,WEEK}；date 未传取该 (site,view) 在 `site_data_stats` 的最新日期，传了则校验确在 `site_data_stats` 内（否则明确报错，不静默回退）
- [ ] 1.3 请求/响应 schema 重构（`app/table/search/search_schemas.py`）：请求含 site/view/date + 筛选 + 排序 + 分页；行 DTO 分 `DayRowItem`（含 trend、week_change）与 `WeekRowItem`；作用域回显 meta（生效 site/view/date + 可选日期集）

## 2. A 期：核心作用域查询（可用分析接口）

- [ ] 2.1 CRUD 重写（`analysis_crud.py`）：base query = `FROM fact_<view> WHERE site=? AND <date_col>=?`，站点/日期边界恒定；JOIN `dim_keyword`（keyword 文本 + 子串搜索走 trgm）、LEFT JOIN `dim_product`（top1 → brand/category/title/asin 子串搜索）
- [ ] 2.2 筛选叠加（AND，不突破边界）：关键词/品牌/类目/ASIN/标题子串、排名区间、排名变化区间、份额区间、is_new
- [ ] 2.3 排序白名单（按视图）：允许 current_ranking/ranking_change/previous_ranking/top1_click_share/top1_conversion_share/is_new；非法字段回落 `current_ranking ASC`；日视图拒绝按走势/周变化排序
- [ ] 2.4 分页总数：无筛选读 `site_data_stats.row_count`（O(1)）；有筛选用 `LIMIT 10000` 上限精确计数（上限可配置）
- [ ] 2.5 日期选择器接口：读 `site_data_stats` 返回该 (site,view) 可选日期集（倒序）
- [ ] 2.6 Service 层组织事务与调用（`analysis_service.py`），API（`analysis_api.py`）改瘦控制器，列表出口走阶段三 `ProTableResponse`；作用域回显随响应返回
- [ ] 2.7 切换/翻页语义：切站点/视图/日期回第 1 页（前端配合，后端保证每页只含当前作用域）

## 3. B 期：日视图附加信息（7 天走势 + 当周排名变化）

- [ ] 3.1 先分页拿本页 keyword_id，再批量补附加，避免整切片相关子查询
- [ ] 3.2 近 7 天走势：`fact_daily WHERE site=? AND keyword_id=ANY(:ids) AND report_date BETWEEN :date-6 AND :date`（走 `idx_fact_daily_keyword_history`），服务层按 keyword_id 归并成走势数组；历史不足 7 天不补齐、不报错
- [ ] 3.3 当周排名变化：先核对导入侧 `report_date_week` 的周日期口径，据此把所选日期映射到所属周；`fact_weekly WHERE site=? AND report_date_week=? AND keyword_id=ANY(:ids)` 取 ranking_change；当周周报缺失则该列为空
- [ ] 3.4 附加信息只按 keyword_id 关联本页行，保证不改变结果集行数/构成；仅周报存在的关键词不进日视图

## 4. C 期：类目选项与打磨

- [ ] 4.1 类目下拉：读 `category_stats` 物化视图，按 (site, period) 返回选项 + 计数；某站点无数据返回空列表
- [ ] 4.2 黑名单类目默认过滤（Books/Grocery 等，沿用 V1 常量清单）
- [ ] 4.3 字段命名与前端阶段四对齐定稿（写进 spec 的响应约定），确认无字段漂移

## 5. 测试（集成，`TEST_DATABASE_URL` gate）

- [ ] 5.1 作用域边界：任意筛选/排序/分页下，每行的 site 与 date 恒等于请求作用域
- [ ] 5.2 日期选择器：只列有数据的日期；未传默认最新并回显；越界日期明确报错不回退
- [ ] 5.3 日视图走势：按所选日期回溯 7 天；历史不足不补齐；当周周报缺失周变化为空；仅周报关键词不入日视图
- [ ] 5.4 周视图：含该周全量（含无日报的关键词）；不受日报影响
- [ ] 5.5 排序白名单：日视图拒绝走势/周变化排序回落默认；合法字段生效
- [ ] 5.6 总数：无筛选等于 `site_data_stats`；周视图总数远大于日视图；带筛选按策略计算
- [ ] 5.7 ProTable 契约形状 + 失败形状（`success:false`）

## 6. V1 查询路径退役

- [ ] 6.1 移除/替换 `analysis_crud/service` 中对 `AmazonOriginSearchData` 的查询（V1 `analysis_model.py` 的最终删除随 amis 收尾，见前端迁移阶段七）
- [ ] 6.2 更新 `CLAUDE.md`：分析查询走 V2 星型模型、作用域为「站点+视图+日期」、日期选择器/总数走 `site_data_stats`、类目走 `category_stats`
