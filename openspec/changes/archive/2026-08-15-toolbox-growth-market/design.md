## Context

V2 星型模型已落地，日报每站每期约 30 万行，保留窗口 3 个月。「导入时算定、查询侧只读」
在 `fact_dropped` 上已经跑通一轮：掉榜集合原本要在查询侧对 7 期切片做反连接（天 180 万行），
且**每翻一页重算一次**，改为导入收尾算定后成本从「每次查询」降到「每期一次」。

持续增长市场的口径与掉榜同构——都是锚定期的一项事实、锚定期一旦确定就不再变化，因此
沿用同一模式，不引入新的架构。

口径已与需求方逐条确认（issue #10 列出的四处歧义）：

| 歧义 | 结论 |
|---|---|
| 「增长」方向 | 名次前进。库里 `ranking_change` 为负表示上升，判定用 `rank(d-6) − rank(d)` 取正值 |
| 累计还是每天 | **累计**净增 > 1 万名，叠加**每日**环比 ≥10% |
| 增长率分母 | 前一期排名，日环比；窗口内每个环比都要满足，不是平均值 |
| 中途掉榜 | 直接出局，不插值、不跳期续连 |
| 窗口期数 | 7 期 → 6 个环比。不为凑满 7 个环比扩到 8 期 |

## Goals / Non-Goals

**Goals:**

- 榜单结果在导入收尾时算定，查询侧只读取，翻页不产生重算。
- 新表随保留窗口自动清理，不需要为它单独写清理逻辑。
- 阈值集中在服务端配置，界面展示的口径文案与判定用的数值同源。
- 工具箱菜单容器与占位屏就位，专利/商标各自另立变更时不必再动菜单结构。

**Non-Goals:**

- 不做任意窗口、任意阈值的即席查询。产品形态是预设窗口 + 固定指标（对标卖家精灵），
  不上 OLAP。
- 不做周报的「持续增长」。周报没有「连续 7 天」这个概念。
- 不实现外观专利与全球商标库。仓库里没有任何专利或商标数据，导入链路只认 ABA 报表的
  21 列。
- 不在榜单上叠加类目、品牌、份额等筛选。榜单是一份定期产出的固定清单，加筛选等于把
  分析屏的整套过滤逻辑复制一份。

## Decisions

### 决策 1：只存命中行，不存全量指标

**选择**：`fact_growth` 每期只写命中口径的那几百~几千行。

**备选**：存下所有「连续 7 天在榜」的词及其两个指标，查询侧再按阈值筛。好处是改阈值
即时生效、不必回填。

**否决理由**：连续在榜的词约占每期的 60~70%，即每站每期约 20 万行；3 站 × 90 天约
5400 万行——为了一个以季度计频率的阈值调整，长期背一张与事实表同量级的表。改阈值
时跑回填并不痛：保留窗口内的 `fact_daily` 都还在，逐期重算即可，而回填脚本无论如何
都要有（否则上线当天三个月历史一条榜单都没有）。

**同时否决**：在 `fact_daily` 上加布尔列打标记。它违反「事实表只追加、不原地更新」这条
不可违反的约束，而且榜单要展示的起止排名、累计净增、最低环比无处安放。

### 决策 2：一条库内窗口函数 SQL，不把行读进应用进程

```sql
WITH win AS (
  SELECT keyword_id, report_date, current_ranking,
         lag(current_ranking) OVER w             AS prev_ranking,
         first_value(current_ranking) OVER w     AS start_ranking,
         count(*) OVER (PARTITION BY keyword_id) AS days_on
    FROM analysis.fact_daily
   WHERE site = :site AND report_date BETWEEN :win_start AND :anchor
  WINDOW w AS (PARTITION BY keyword_id ORDER BY report_date)
)
SELECT ... FROM win WHERE days_on = :window_days GROUP BY keyword_id HAVING ...
```

`days_on = 7` 一条同时兑现两件事，不必单独查 `fact_dropped`：

- **中途掉榜自动出局**——掉的那期没有行，凑不满 7。
- **窗口内缺期时整期榜单为空**——所有词都凑不满 7。这是据实反映，不是 bug；缺期本身
  由响应回传、界面明示。

`first_value` 用默认帧（`RANGE UNBOUNDED PRECEDING`）取分区首行，即 `d-6` 的排名。

**扫描范围与执行计划**：`WHERE site = :site AND report_date BETWEEN ...` 命中月度 RANGE
分区 + 站点 LIST 子分区，扫的是 7 期 × 30 万 ≈ 210 万行（单站）。排序键 `(keyword_id,
report_date)` 与 `idx_fact_daily_keyword_history (site, keyword_id, report_date)` 的列序
一致，可走索引序、免排序节点；即便回落到外部排序，PostgreSQL 落盘处理，**内存有界**。
这段跑在导入事务里，与 GB 级 COPY 同一条同步连接上，**不新增连接池占用**。

**跨月边界**：窗口可能横跨两个月度分区（如锚定日 8-03，窗口回到 7-28）。RANGE 分区
天然支持跨分区扫描，无需特殊处理；但回填脚本要能处理「窗口起点早于最早分区」的情形
——那种期次窗口不完整，跳过。

### 决策 3：不给 `site_data_stats` 加第三个计数列

`site_data_stats` 存在的理由是躲开 8 亿行事实表上的实时聚合。`fact_growth` 每期只有
数千行，`count(*)` 走 `(site, anchor_date)` 索引前缀是毫秒级，不构成同一个问题。加列
的代价是一条迁移 + 列注释同步 + 与事实写入的一致性约束（那两个预存值是分页分界线，
错位会让某一行出现两次），换来的收益是零。

日期选择器同样不需要新数据源：「哪些日期有榜单」= 「哪些日期有日报」，直接复用
`site_data_stats` 的 DAY 期次列表。

### 决策 4：走势查询侧补，不进表

每行要画 7 点走势。**不**在 `fact_growth` 里存排名数组——V1 的 `ranking_trend_day`
JSONB 正是这么长出来的，时序化之后它是纯冗余。改用分析屏已验证的「先分页再批量补附加」：
当页 ≤100 行，一次 `WHERE site=? AND keyword_id = ANY(?) AND report_date BETWEEN ?`
走 `idx_fact_daily_keyword_history`，7 × 100 = 700 行。

与分析屏那个坑的区别：那里回溯范围（84 天）宽于趋势窗口（4 周），所以 `shape_trends`
必须裁剪；**这里回溯范围与窗口完全相同**（都是 7 期），没有裁剪需求，也就没有那个
「画出 8 个点而无人报错」的失败模式。

### 决策 5：新模块 `app/table/toolbox/`，但模型寄居在 analysis

- **模型** `fact_growth` 放 `app/table/analysis/fact_model.py`，与 `fact_dropped` 并列。
  分区注册表、保留清理、导入收尾都在 analysis 域内，模型跟着它们走，登记一处即被三处接管。
- **查询层** 新建 `app/table/toolbox/`（`growth_crud/service/api/schemas`）。「工具箱」
  是界面容器，以后要装专利、商标，那些与 ABA 分析无关；把它们塞进 `analysis/` 会让那个
  模块变成杂物间。

路由挂 `/api/toolbox`，全部 `require_permission("toolbox:view")`——**没有全局鉴权中间件，
漏挂即裸奔**。

### 决策 6：Alembic 迁移手写

分区表不在 SQLAlchemy 的 DDL 生成范围内，autogenerate 会把 `fact_growth` 产成普通表并
**静默丢掉分区**。因此手写迁移，并用 `alembic upgrade head --sql` 核对生成的 DDL 里
确有 `PARTITION BY RANGE (anchor_date)`。

迁移内容（全部 schema-qualified 到 `analysis`）：

1. `CREATE TABLE analysis.fact_growth (...) PARTITION BY RANGE (anchor_date)`
2. `CREATE INDEX idx_fact_growth_rank ON ... (site, anchor_date, rank_gain DESC)`
3. 种 `toolbox:view` 权限点，并给 admin / operator / analyst 三个内置角色各插一条
   `role_permission`。迁移不 import 应用代码，按 `permissions.py` 同一份矩阵硬编码
   （与既有权限种子迁移的做法一致）。

**不建当月分区**——分区由 `ensure_fact_partitions` 在导入/回填时按需创建，迁移里建
等于把「哪个月」这件事写死在迁移时刻。

**降级**：`DROP TABLE analysis.fact_growth`（连带全部子分区）+ 删权限点与其角色关联。

模型登记两处：`partitioning.PARTITIONED_TABLES`（`PartitionScheme("anchor_date",
site_subpartition=False)`）与 `migration_scope.V2_TABLES`。漏登 `V2_TABLES` 的表
autogenerate 既看不见也不报错，`test_conventions.py` 会拦住。

### 决策 7：阈值进 config，不进表

`GROWTH_WINDOW_DAYS=7` / `GROWTH_MIN_RANK_GAIN=10000` / `GROWTH_MIN_DAILY_RATE=0.10`，
同步 `.env.example`（含单位与安全默认值）。由 `/scope` 端点回传给界面，口径说明条里的
数值不在前端硬编码——两份副本分叉正是 `GB`/`UK` 那类问题的由来。

**已算定的行不记录当时的阈值**。改阈值后旧期次的榜单仍是按旧阈值算的，必须跑回填重算。
这条写进配置项注释与回填脚本的帮助文本。

## Risks / Trade-offs

- **[导入变慢]** → 每期多一次 210 万行的窗口聚合。日报总耗时约 4 分钟，这段预计几秒
  量级（同数据量的掉榜反连接实测在秒级）。周报不受影响（不参与）。实施时实测并记录，
  若超过 30 秒则改为在事实事务提交后独立进行。
- **[改阈值需要回填]** → 决策 1 的自觉代价。回填脚本是交付物之一，帮助文本里写明这条。
- **[缺期时榜单为空被误读为「今天没有增长词」]** → 缺失期次随响应回传，界面顶部明示。
  这是 spec 里的一条正式要求，不是可选的体验优化。
- **[补导旧期后既有榜单不再正确]** → 与「只追加、算定不回改」一致，是有意的取舍。
  文档写明，回填脚本可指定日期范围重跑。
- **[占位屏被误当作功能缺陷]** → 占位屏必须写明「数据源尚未接入」，不用泛泛的空状态。

## Migration Plan

1. 建表迁移 + 权限种子迁移（可合并为一条），`--sql` 核对分区 DDL。
2. 部署后端与前端。此时榜单表为空，界面显示缺期/无数据，**不报错**。
3. 运行 `scripts/backfill_growth.py --dry-run` 核对每期预计行数，再去掉 `--dry-run`
   实跑，回填保留窗口内全部日报期次。
4. 此后每次日报导入自动算定当期。

**回滚**：降级迁移删表与权限点；前端菜单随代码回滚消失。事实表未被改动，回滚不损失
任何既有数据。

## Open Questions

无。四处口径歧义与存储权衡均已与需求方确认。
