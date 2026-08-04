> 认领：`change/keyword-level-brand-category` 分支 + `~/.claude-worktrees/` 下的 worktree，
> 2026-08-04 开工。进度以本文件为准，勾选与说明必须一致。

## 0. 前置：归档顺序

- [x] 0.1 **`show-top3-products` 与 `show-search-trend-in-week-view` 已归档**（v2 上的
      00445e6），本分支已合入 v2，delta 标题与 Scenario 名均已重对齐归档后的主 spec。
      归档时连吃五次拦截，全因 MODIFIED 是**整条替换**、块里没写的 Scenario 会被丢掉。
      本变更的 delta 已按同样标准自查：三处标题改名走 RENAMED，各条 Scenario 取主 spec
      的超集。那条「行字段」在主 spec 里现已更名为「两视图的行字段各自明确」

## 1. 数据模型

- [x] 1.1 `dimension_model.py` 新增 `DimBrand`（`brand_id integer` PK、`name` 唯一）与
      `DimCategory`（`category_id smallint` PK、`name` 唯一）。类型按基数分：品牌 13.5 万/站/天，
      类目 104
- [x] 1.2 `fact_model.py` 两张事实表各加 6 列：`top1..3_brand_id integer NULL`、
      `top1..3_category_id smallint NULL`。**先不加索引**（见第 4 组）
- [x] 1.3 `dim_product` 删 `brand` / `category` 两列，收敛为 ASIN + 标题；同时删
      `idx_dim_product_brand_trgm` / `idx_dim_product_category_trgm`

> **当前状态：前后端已拉齐（第 1、2、3、5、6 组）。** 后端 315 passed / 1 skipped，
> 前端 lint/tsc/117 passed。剩余：第 4 组（索引实测）、第 7 组（重导脚本）、
> 第 8 组（停机发布）。
> 第 5 组先于第 4 组做——它把 21 个红转绿、恢复了可验证的基线，而第 4 组的 EXPLAIN
> 实测需要真实数据量，本就该排在重导之后。剩余：第 4 组（索引实测）、第 6 组（前端）、
> 第 7 组（重导脚本）、第 8 组（停机发布）。**前端未动，分支仍不可合回 v2。**

## 2. 迁移（手写，分区表 + 物化视图不在 autogenerate 范围）

- [x] 2.1 建两张维度表
- [x] 2.2 事实表父表 `ADD COLUMN <type> NULL`——**不给 DEFAULT**，否则 PG 会重写 2 亿行；
      无默认值时是纯元数据操作，且自动传播到全部子分区
- [x] 2.3 `DROP MATERIALIZED VIEW category_stats` 并按关键词级类目重建；新增 `brand_stats`
      （结构与 `category_stats` 同构，供品牌下拉）
- [x] 2.4 删 `dim_product` 两列与两个 GIN 索引
- [x] 2.5 **`downgrade` 必须能把两列与两个 GIN 索引全部恢复**——数据回不来（本就是错的），
      但 schema 要回到旧代码可运行的形状
- [x] 2.6 `alembic upgrade head --sql` 离线核对：确认事实表 DDL 里 `PARTITION BY` 未被破坏
- [x] 2.7 本地 `downgrade -1 && upgrade head` 跑通（在**本 worktree 专属库**
      `keywordiq_brandcat` 上，没碰共享的 `keywordiq_test`——drop 前先查了
      `pg_stat_activity`）。真库核对：`fact_daily` relkind 仍是 `p`、12 个新列、
      **无一带默认值**、`dim_product` 残留 0 列、两个物化视图齐备；downgrade 后两列与
      两个 GIN 索引均恢复

## 3. 导入链路

- [x] 3.1 `_upsert_dimensions` 增加品牌与类目两个维度的 upsert（源列 c2–c4 / c5–c7），
      在 `_import_staging` 内以 SQL 完成，不进应用内存
- [x] 3.2 `_load_facts` 补 6 个引用列
- [x] 3.3 `_upsert_dimensions` 里 `dim_product` 的三段 UNION 去掉 brand / category
      ——**这是缺陷的根，改完它 `dim_product` 才不再被写入伪造属性**
- [x] 3.4 `import_service._refresh_category_stats` 一并刷新 `brand_stats`
- [ ] 3.5 用真实体量 CSV 跑 `verify-import` skill，断言六个新列落库正确

## 4. 索引：先实测再定，不许凭直觉建

- [ ] 4.1 在真库上准备可测数据量（重导至少一期真实日报）
- [ ] 4.2 对方案 A（三个独立 btree + BitmapOr）与方案 B（`ARRAY[b1,b2,b3]` 表达式 GIN）
      各跑 `EXPLAIN (ANALYZE, BUFFERS)`，记录：单值筛选、多选筛选、索引体积、导入后建索引耗时
- [ ] 4.3 据实测落定方案，**把数据回填到 design.md 决策 4**
- [ ] 4.4 迁移里建索引（本任务被 4.3 阻塞——V1 有过投机建索引、实测 0 命中的先例）

## 5. 查询层

- [x] 5.1 `_base_query` 关联两个新维度（六个槽位），行数据补品牌/类目的三组字段
- [x] 5.2 `_apply_filters` 的品牌与类目改为**前三任一命中**；品牌由子串改为精确多选
      （与类目对称），请求字段 `brands: list[str]`
- [x] 5.3 `search_schemas` 增删对应字段；移除旧的 `brand` 子串参数
- [x] 5.4 品牌下拉端点（读 `brand_stats`），与 `/categories` 同构
- [x] 5.5 导出增加两组按名次的列
- [x] 5.6 集成测试：构造「品牌 X 只出现在第 2 槽位」的样本，断言按 X 筛选能命中——
      **这是能证明"前三任一命中"真的生效的唯一断言**，只测第 1 槽位的话与旧行为无法区分

## 6. 前端

- [x] 6.1 结果表格加「点击前三品牌」「点击前三类目」两列，按名次竖排，与商品卡牌名次对齐
- [x] 6.2 品牌筛选改为多选下拉（`BrandPanel`，与 `CategoryPanel` 同构），**选项必须带计数**
      ——用户面对 Anker / Anker Direct / Ankermann 时需要计数才能判断选哪个
- [x] 6.3 移除过滤区的品牌子串输入框（一个维度一个入口）
- [x] 6.4 **收窄**（不是移除）`show-top3-products` 留下的「展示口径宽于筛选口径」标注：
      品牌与类目已对齐故去掉，但 **ASIN 与商品标题仍只匹配第一名**、表格仍展示前三，
      那两条标注必须留着。delta 原本整条 REMOVED 是我写错了，已改为 MODIFIED
- [x] 6.5 `AnalysisRow` 扩字段；`filters.ts` 的转换与测试同步

## 7. 历史数据重导

- [ ] 7.1 按批次重导脚本：读 `import_batch.object_key` 从 OSS 重导，**可中断续跑**、
      记录进度。热窗口 3 个月 ≈ 90 个日报 + 13 个周报批次
- [ ] 7.2 未重导日期在界面明确提示「该日期数据待重建」，**不静默返回空**——
      静默是最难排查的形态，用户会以为是筛选条件写错了
- [ ] 7.3 重导走同一条 `pg_try_advisory_lock`，与常规导入天然串行，不需额外协调

## 8. 发布（停机）

- [ ] 8.1 **低峰停机发布**：停 uvicorn → `deploy.sh` → 起 uvicorn。删列迁移一跑完，
      仍在运行的旧 worker 就面对缺列的 schema，此后每个查询 500，窗口 ≈ SPA 构建时长。
      这一步写在这里就是因为「记得先停应用」最容易在深夜漏掉
- [ ] 8.2 起服务后先验证一个品牌筛选与一个类目筛选，再宣布完成
- [ ] 8.3 重导脚本按 7.1 推进，完成前保留 7.2 的提示

## 9. 收尾

- [ ] 9.1 全量回归：`ruff` + `pytest`（**全新库首跑**，含 `TEST_DATABASE_URL` 集成）
      + 前端三项 + `openspec validate --strict`
- [ ] 9.2 `openspec archive`，delta 合入主 spec
