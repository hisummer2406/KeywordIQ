## 1. 前置核查与容量定档

- [ ] 1.1 用 V1 生产表实测单行字节数（`pg_total_relation_size / count(*)`），校准 design.md 的容量估算——该估算基于列宽推算，误差可达 ±30%
- [ ] 1.2 按第一阶段规格开通 V2 的 RDS 实例（4 vCPU / 16 GB + ESSD PL1 200 GB），按 design.md 的参数配置。数据从零积累，无需一次性按 8 亿行下单。V1 实例保持不动
- [ ] 1.3 把扩容触发条件写入运维文档并配置告警：磁盘使用率 > 70%，或缓存命中率持续 < 95%。预计第 4 个月前后触发第一次扩容
- [x] 1.4 采集 V1 库 `pg_stat_user_indexes` 的 `idx_scan`（已完成）：热点为日期+排名复合与新词筛选；6 个投机部分索引实测 0 命中；文本搜索 btree 近乎死。据此定型 V2 事实表索引，含 is_new 部分索引
- [ ] 1.5 导出 V1 的 `analysis.my_category_stats` 定义，作为新物化视图的参考
- [x] 1.6 定死站点代码清单与书写标准（ISO alpha-2，明确 `GB` 而非 `UK`），写入 design.md

## 2. Alembic 基线

- [x] 2.1 执行 `alembic init`，配置 `version_table_schema='analysis'`、`include_schemas=True`、`target_metadata=Base.metadata`，连接串复用 `config.py` 而非硬编码
- [x] 2.2 确认迁移中所有对象显式带 `schema='analysis'`，不依赖 `search_path`
- [ ] 2.3 在本地空库执行 `alembic upgrade head` 与 `downgrade base`，确认可从零建出完整结构且可逆
- [x] 2.4 在 README 或 CLAUDE.md 补充迁移操作说明（如何新建、执行、回滚）

## 3. 站点清单配置

- [x] 3.1 新增站点枚举与站点清单配置（Python 侧单一事实来源），提供站点码校验函数。站点清单决定分区创建，属于数据模型的一部分
- [x] 3.2 `config.py` 增加 `ENABLED_SITES` 与 `HOT_WINDOW_MONTHS`，同步更新 `.env.example`（含取值示例与说明）
- [x] 3.3 编写站点校验的单元测试（合法通过、非法抛错、大小写处理），`uv run pytest test/` 验证

## 4. 维度表

- [x] 4.1 `dim_keyword` 模型：keyword_id、site、keyword，`UNIQUE(site, keyword)`
- [x] 4.2 `dim_product` 模型：product_id、asin、title、brand、category，`UNIQUE(asin)`。SCD Type 1，不保留历史版本
- [x] 4.3 `dim_keyword` 上建 pg_trgm 子串索引（`keyword gin_trgm_ops`，匹配界面的 ILIKE 搜索）；事实表不建。`dim_product` 的 title/brand/category/asin 同样建 trigram
- [x] 4.4 `analysis.sites` 参考表模型（站点码、显示名、时区、域名），不建外键

## 5. 事实表与分区

- [x] 5.1 `fact_daily` 模型：site、report_date、keyword_id、排名四件套（current/previous/change/is_new）、三组商品引用与份额，`UNIQUE(site, report_date, keyword_id)`
- [x] 5.2 `fact_weekly` 模型：结构同上，日期字段为 report_date_week
- [x] 5.3 两张事实表建为按 `report_date` 的月度 RANGE 分区 + 按 `site` 的二级 LIST 分区
- [x] 5.4 实现幂等的"确保分区存在"函数：给定站点与日期，自动创建缺失的月份分区与站点子分区。**这是本变更的交付项，不留给导入变更**
- [x] 5.5 按 1.4 的裁剪清单为两张事实表**各自创建完整的一套索引**（两个视图各自独立驱动查询，`fact_weekly` 不是附属表），以 `(site, report_date, ...)` 为前导列，建在分区级
- [x] 5.6 确认两张事实表均无原地更新所需的结构（不设 `fillfactor` 预留、不设 `ranking_trend_day` 之类的冗余派生字段）

## 6. 辅助结构

- [x] 6.1 `analysis.site_data_stats` 模型：site、period_type、report_date、行数、更新时间。同时服务分页总数与日期选择器的可选日期
- [x] 6.2 `import_batch_records` 模型重建：含 site、period_type、report_date
- [x] 6.3 类目物化视图：按 `(site, period_type, top_category)` 聚合，由两张事实表关联 `dim_product` 后 `UNION ALL`；建唯一索引以支持 `REFRESH ... CONCURRENTLY`

## 7. 验证（用小样本，几千行足矣）

结构正确性——分区裁剪、跨月建分区、唯一约束、执行计划顺序——在几千行上就完全看得出来，
与最终数据量无关。这一组只需一个本地 PostgreSQL（Docker 或本机装一个），不需要 V2 实例、
不需要造大数据。

- [x] 7.1 在本地 PostgreSQL 执行 `alembic upgrade head` 与 `downgrade base`，确认全部对象可从零建出且可逆
- [x] 7.2 灌入几千行小样本（覆盖 2 个站点、跨 2 个月、日报+周报），验证分区裁剪：`EXPLAIN` 确认单站单日查询只触及一个叶子分区
- [x] 7.3 验证"确保分区存在"函数的幂等性：重复调用不报错、不重复创建
- [x] 7.4 验证跨月自动建分区：插入一条落在新月份的样本数据，确认分区被自动创建
- [x] 7.5 验证唯一约束：同站点同日期同关键词不可重复；不同站点、不同日期、不同周期可共存
- [x] 7.6 编写模型层单元测试（约束定义、分区函数逻辑），`uv run pytest test/` 验证，不依赖真实 PostgreSQL

## 8. 上线

- [ ] 8.1 用 V1 生产表实测单行字节数（`pg_total_relation_size / count(*)`），确认 design.md 的容量估算量级无误
- [x] 8.2 V1 `idx_scan` 已采集并据此定型事实表索引（见 1.4 与 design 决策 6），不再是暂定方案
- [ ] 8.3 导出 V1 的 `analysis.my_category_stats` 定义，与新物化视图比对语义
- [ ] 8.4 开通 V2 实例（4 vCPU / 16 GB + ESSD PL1 200 GB 起步，数据从零积累），配置扩容告警：磁盘 > 70% 或缓存命中率持续 < 95%
- [ ] 8.5 在 V2 实例执行建表迁移，部署应用后即可按站点开始导入（导入逻辑属变更 2）

> 已删除"灌 8 亿行做验证"的任务。8 亿行是 12 个月后自然增长到的规模，不是上线要制造的东西。
> `scripts/generate_synthetic_data.py` 保留为**可选的压测/容量工具**，谁需要在真实规模上测
> 查询性能时再用（默认 `--scale` 小、需 `--yes` 确认），它不是任何上线步骤的前置。
