## 0. 开工前置

- [x] 0.1 确认 `async-query-path` 的**代码部分**已合入 `v2`（`analysis_api` 已是 `async def`）。其剩余 4 项全在第 7 组「部署与实测」，无一碰代码，且在 V2 整体发布前本就完不成——不作为本变更的前置条件
- [x] 0.2 按 `start-change` 建仓库外 worktree `~/.claude-worktrees/redesign-day-week-window-view` 与分支 `change/redesign-day-week-window-view`（从含本变更文档的提交 `c8aa639` 开出，非从 `v2`，否则 openspec 文档会落下）；已补 `.env`、`frontend/node_modules`、专属库 `keywordiq_redesign_day_week_window_view`（已 `alembic upgrade head`）
- [x] 0.3 基线回归：`328 passed, 1 skipped`（唯一跳过是需 `TEST_OSS_*` 的真实 OSS 回路，与本变更无关）。集成测试确认在跑，不是静默全跳过

## 1. 数据模型与迁移

- [ ] 1.1 在 `app/table/analysis/fact_model.py` 定义 `FactDropped`（`site`/`period_type`/`anchor_date`/`keyword_id` 主键，`last_seen_date`/`last_ranking`/`created_at`，月度 RANGE 分区声明），并加 `(site, period_type, anchor_date, last_ranking)` 索引
- [ ] 1.2 在 `SiteDataStats` 加 `dropped_count`（BigInteger，非空，默认 0）
- [ ] 1.3 手写 Alembic 迁移建 `analysis.fact_dropped`（含 `PARTITION BY RANGE (anchor_date)`、父表索引、当前月及前两月初始分区）
- [ ] 1.4 手写 Alembic 迁移给 `site_data_stats` 加列
- [ ] 1.5 用 `uv run alembic upgrade head --sql` 核对生成的 DDL 里确有 `PARTITION BY`，并确认两条迁移都限定在 `analysis` schema
- [ ] 1.6 扩展 `partitioning.ensure_fact_partitions`，让它一并确保掉榜表的月度分区存在（幂等）
- [ ] 1.7 在 `retention_service` 把掉榜表纳入按月 `DROP` 的清单
- [ ] 1.8 测试：迁移可 upgrade / downgrade 往返；`ensure_fact_partitions` 建出掉榜分区；保留清理连带移出掉榜分区（`uv run pytest test/`）

## 2. 导入侧：掉榜集合算定

- [ ] 2.1 在 `app/table/upload/importer.py` 加掉榜集合计算（`DISTINCT ON (keyword_id)` + `NOT EXISTS` 反连接，`INSERT ... SELECT` 纯库内完成，不读进应用进程），窗口长度按周期取（日 7 天 / 周 4 周）
- [ ] 2.2 同一期重复导入时先删该期掉榜记录再重算，与事实数据的重复导入处理保持一致
- [ ] 2.3 在 `import_service._update_site_data_stats` 一并写入 `dropped_count`，与事实写入同一事务提交
- [ ] 2.4 测试：掉榜集合正确性（窗口内掉出的在、窗口外掉出的不在、在榜的不在）、最后在榜期取窗口内最近一期、重复导入不产生重复记录（`uv run pytest test/`）
- [ ] 2.5 用 `verify-import` 在真实体量报表上各跑一次日报与周报导入，**记录掉榜集合计算的实测耗时与掉榜行数**，回填 design.md 的 Open Questions
- [ ] 2.6 若周视图耗时超出可接受范围，按 design 的回退方案把周行窗口降到 2 周，并同步改规格与文档

## 3. 查询侧：行集合与分段分页

- [ ] 3.1 在 `search_schemas` 加 `include` 字段（`ALL` / `LISTED` / `DROPPED`，缺省 `ALL`，非法值按参数不合法拒绝）
- [ ] 3.2 在 `analysis_crud` 加掉榜段的查询语句与计数语句（走 `(site, period_type, anchor_date, last_ranking)` 索引，索引顺序即分页顺序）
- [ ] 3.3 在 `analysis_crud._apply_order` 落实「掉榜恒在在榜之后、不随排序方向翻转」
- [ ] 3.4 在 `analysis_service.search` 实现分段分页编排（四种边界情形按 design 决策 5），带筛选时掉榜段短路为 0
- [ ] 3.5 总数改为在榜数 + 掉榜数（无筛选读两个预存值；带筛选走现有精确计数，掉榜计 0）
- [ ] 3.6 把趋势回溯范围扩到覆盖最远参照点（天 `[d-7, d]` 8 期、周 `[w-11, w]` 12 期），并从回溯结果中取出三个历史参照点的排名，**不新增查询**
- [ ] 3.7 行响应加 `is_dropped` / `last_seen_date` / `last_ranking` / 三个历史排名字段；掉榜行的当期字段（当期排名、商品槽位、品牌、类目、份额）一律为空，不用最后在榜期的值顶替
- [ ] 3.8 把排序白名单里的趋势与历史排名字段明确排除
- [ ] 3.9 导出沿用同一份行集合与 `include` 参数；行数上限判定覆盖两段之和，且在开始流式输出之前完成
- [ ] 3.10 测试：四种分页边界（整页在前段 / 正好跨在分界线 / 整页在后段 / 两段各出一半）无重复无遗漏（`uv run pytest test/`）
- [ ] 3.11 测试：带筛选时掉榜行不出现且总数不含它们；`include` 三种取值各自的行集合与总数正确（`uv run pytest test/`）
- [ ] 3.12 测试：历史参照期缺失（未导入或已被保留清理）时该值为空、查询不报错；掉榜行也带趋势（`uv run pytest test/`）
- [ ] 3.13 测试：没有掉榜记录的历史期只返回在榜行、不报错（`uv run pytest test/`）

## 4. 前端

- [ ] 4.1 `services/analysis.ts` 补 `include` 请求字段与新增的行字段类型
- [ ] 4.2 `filters.ts` 把行集合开关纳入条件转换（纯函数，vitest 直接测）
- [ ] 4.3 `FilterPanel.tsx` 加行集合开关，与其余筛选条件分区呈现；切到「仅掉榜」时其余筛选置灰并给出说明
- [ ] 4.4 筛选生效时在界面明示「本次结果不含掉榜行」及其原因
- [ ] 4.5 `columns.tsx`：现排名列吃掉「新词」独立列并容纳「已掉榜」标签 + 最后在榜期，两标签互斥
- [ ] 4.6 `columns.tsx`：新增「历史排名」列（单列三行，标签按视图取），参照期缺失显示占位符且不隐藏该行
- [ ] 4.7 `columns.tsx`：新增「排名变化率」列，由排名变化与上期排名在展示层算出，符号与配色和「排名变化」列完全一致；上期排名为空或为零时显示占位符
- [ ] 4.8 趋势小图周视图渲染 12 点（回溯已在 3.6 扩好），悬浮提示复用现有的整周区间格式化函数
- [ ] 4.9 测试：`filters.ts` 与 `columns.tsx` 的 vitest 覆盖新增分支（掉榜行渲染、变化率符号与「排名变化」同向、参照期缺失占位）

## 5. 文档与收尾

- [ ] 5.1 改写 CLAUDE.md「V2 查询层」一节：作用域从「单一事实切片」改为「单一站点 + 锚定期 + 行窗口」，并写明带筛选路径仍锁死在单一切片
- [ ] 5.2 CLAUDE.md 的 V2 表清单加 `fact_dropped` 及其分区约定；「事实表只追加不原地更新」那条约束的适用范围写明含掉榜表
- [ ] 5.3 回填 design.md 的两个 Open Questions（实测耗时与掉榜行数量级）
- [ ] 5.4 跑一次含集成测试的全量回归（`local-db` skill），确认不是 `181 passed, 45 skipped` 那种把集成部分整片跳过的假绿
