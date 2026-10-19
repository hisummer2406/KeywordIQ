## Context

现状（`importer._load_facts`）：`is_new = 本期之前 7 天内同站点同周期表里没出现过`，写进事实行。三个问题：只看滚动窗口（掉榜 8 天再回来又是新词）；日报、周报各查各的表；事实表只留 3 个月，窗口外的历史对它不存在。#137 要的「首次入榜」是**永久、日周共享、按站点**的一个事实。

关键观察：`dim_keyword` 以 `(site, keyword)` 唯一、不分区、不参与保留清理、行只增不删——它**本来就是“这个站点见过哪些词”的永久集合**。一个词“不在 `dim_keyword` 里”就等于“从未见过”，不需要再造一张历史表，只需让这张表记住“第一次是哪一期”。

约束：事实表只追加不原地更新（所以首次信息不能写进事实行，补历史时它会变）；`dim_keyword` 约 1500 万行，迁移不能在 alembic 里做长事务全表回填；GIN 索引已很重，不再给 `dim_keyword` 加写放大大的索引。

## Goals / Non-Goals

**Goals:**
- 永久首次记录，独立于保留期；日、周同一份；站点隔离。
- 重复导入、重试、乱序、日周交叉导入结果一致；补更早数据只会把首次日期往前修。
- 存量不产生“上线日全量首次入榜”；未知如实标为未知。
- 市场分析页的筛选/列/导出/当期数量同一口径。

**Non-Goals:**
- 增长市场与趋势新词识别（#138）。
- #119 里其余事项（数量对照、7 天变化率、类目黑名单等）。
- 回溯重建已被清理的历史——无证据就是 `UNKNOWN`/`EARLIEST_OBSERVED`，不伪造。

## Decisions

### D1：首次记录放在 `dim_keyword`，三列

| 列 | 含义 |
|---|---|
| `first_seen_date date NULL` | 首次出现的报告期次日期（取自文件名日期，与分区归属同源）；`UNKNOWN` 时为 NULL |
| `first_seen_period_type varchar(4) NULL` | `DAY`/`WEEK`：这个日期来自日报还是周报，决定能不能当“某一天” |
| `first_seen_basis varchar(20) NOT NULL` | `CONFIRMED` 已确认首次 / `EARLIEST_OBSERVED` 最早可观测（此前可能还见过） / `UNKNOWN` 无证据 |

只有 `CONFIRMED` 才计入“首次入榜”。**备选**：新开 `keyword_first_seen` 表——否决，多一次 join 且与 `dim_keyword` 一一对应，没有独立生命周期。**备选**：写进事实行——否决，违反“事实只追加”，补历史时需回写 8 亿行。

时间用 `date` 不用时间戳：业务时间就是报告期次日期，没有时区问题，也不会被上传时间/迁移时间污染。

### D2：什么时候算 `CONFIRMED`

导入补齐维度时，按“这是不是站点的最早已知期次”分流：

- **基线期**：该站点 `dim_keyword` 里没有任何 `first_seen_date`，或本次 `report_date <= min(first_seen_date)`（站点最早已知期次及更早的补历史）。该期里**新插入**的词记 `EARLIEST_OBSERVED`。理由：基线期之前没有对照物，说“首次”是在没有证据的情况下下判断——否则新站点首导、补更早报告会让整期涌进“首次入榜”，而且没有任何报错。
- **其他情况**：新插入的词（`dim_keyword` 里不存在）记 `CONFIRMED`，日期=`report_date`，类型=本次周期。
- 判基线只读 `dim_keyword`（永久），**不读 `site_data_stats`**——后者会被保留清理，早期统计行被清掉会让“最早期次”后移，把中途导入误判成基线。

**一个已知边界**：站点首次引入某个词之后，该词若在保留窗口里被清理、又被重新导入，因 `dim_keyword` 行永不删除，不会被重判。而“上线前已被清理且不在 `dim_keyword`”的词不存在——`dim_keyword` 从未清理过。

### D3：已存在的词怎么更新（乱序、补历史、日周交叉）

对本次暂存里**已存在于 `dim_keyword`** 的词，只在证据更早时修正：比较键 `(first_seen_date, rank(type))`，`DAY=0 < WEEK=1`，新键更小才更新：

```sql
UPDATE analysis.dim_keyword k
   SET first_seen_date = :rdate,
       first_seen_period_type = :period,
       first_seen_basis = CASE WHEN k.first_seen_basis = 'UNKNOWN'
                               THEN 'EARLIEST_OBSERVED' ELSE k.first_seen_basis END
  FROM (SELECT DISTINCT keyword FROM _import_staging) s
 WHERE k.site = :site AND k.keyword = s.keyword
   AND (k.first_seen_date IS NULL
        OR (:rdate, :rank) < (k.first_seen_date, rank_of(k.first_seen_period_type)))
```

- **同日并列日优先于周**：日报与周报同一日期时必须有确定结果，否则“周报先导入”与“日报先导入”会得到不同的类型、进而不同的日视图计数。
- **后导入的日报可以细化先导入的周报**：周报 `w` 记 `(w, WEEK)`；之后导入 `d ∈ [w-6, w]` 的日报，`d < w` 则更新为 `(d, DAY)`。周视图两种都落在同一周窗内，计数不变，日视图因此多出精确到日的一条——细化，不是矛盾。
- `UNKNOWN → EARLIEST_OBSERVED`：该词迁移时已存在、没有证据；现在见到它了，所以它“至少在这一期见过”，但它当然不是新词。
- `CONFIRMED` 永不降级；`EARLIEST_OBSERVED` 不会被升成 `CONFIRMED`。重复执行同一期，比较键相等，`UPDATE` 零行（**不写**，与 `dim_product` 的 `IS DISTINCT FROM` 同一条纪律）。
- 事务：与维度补齐同一事务，导入失败整体回滚，不会留下“首次期次=失败的那期”的词。

### D4：日、周映射与当期数量（不存按期统计）

周报期次间隔 7 天（沿用 `importer._PERIOD_CONFIG["WEEK"]` 的 7 天偏移，不新造周定义）。固定映射：**周期 `w` 覆盖 `[w-6, w]`**。

| 视图 / 锚定期 | “首次入榜”命中 |
|---|---|
| DAY，日期 `d` | `basis=CONFIRMED` 且 `type=DAY` 且 `first_seen_date = d` |
| WEEK，周期 `w` | `basis=CONFIRMED` 且 ((`type=WEEK` 且 `date = w`) 或 (`type=DAY` 且 `date ∈ [w-6, w]`)) |

每个词只有一个首次事件，所以 DAY 命中的词在对应 WEEK 视图里**只计一次**（不会“日报事件 + 周报事件”相加）。周报来源的首次**不进任何一天的日视图**——只有周精度，不捏造日期；这使某天的日视图计数可能少于“真实当天新出现”，由界面说明承担。

例（站点 US，均 `CONFIRMED`）：词 A 首见于日报 7/10；词 B 首见于周报 `w=7/13`（覆盖 7/7–7/13）；词 C 首见于日报 7/15。
- 日视图 7/10 → {A}；7/13 → {}（B 没有具体日）；7/15 → {C}。
- 周视图 `w=7/13` → {A, B}；`w=7/20`（7/14–7/20）→ {C}。
- 之后补导入日报 7/9 且含 B：B 变为 `(7/9, DAY)`；日视图 7/9 → {B}；周视图 `w=7/13` 仍是 {A, B}。

**为什么查询时实算，不存按期统计**：命中集合就是 `dim_keyword` 上一段索引范围，大小是单期新词量级（千到万）。按期预存的统计在补历史/乱序时必须校正（issue 要求“明确受影响统计的校正方案”）——不存就没有要校正的东西，方案就是“无”。索引 `idx_dim_keyword_first_seen (site, first_seen_date)`（普通 btree，一次性建在 1500 万行上、此后只有新词才写）同时服务计数、筛选和基线判定的 `min()`。

### D5：查询侧用 join 派生，退役 `fact.is_new`

筛选、列、导出、词库盯盘的“是否首次入榜”改为对 `dim_keyword` 的条件（各查询本就 join 它取关键词文本，主键 join 无额外代价），不再读事实行上的 `is_new`。`is_new` 在事实行是**导入时算定**的，补历史后会变陈旧；放在 `dim_keyword` 上才是“当前最准”的一份。

迁移**删除** `fact_daily`/`fact_weekly` 的 `is_new` 列（分区表 `DROP COLUMN` 是元数据操作，随分区继承），并删除 `scripts/backfill_is_new.py`。**备选**：保留列但不再写——否决，留一个口径已经不同、名字还叫“新词”的列，正是 CLAUDE.md 说的“名不副实”。

接口：请求字段 `is_new` → `first_listed`（布尔，两态）；响应行字段 `is_new` → `first_listed`（该行当期是否命中上表）+ `first_seen_date`/`first_seen_basis`（供界面标注）。排序白名单里与 `is_new` 相关的项同步改名。`SAVED_MODE_VERSION` +1，旧保存条件静默丢弃；`CACHE_VERSION` +1（会话快照形状变了）。

「排除品牌词」仍是首次入榜筛选下的子条件，只在「只看首次入榜」时随请求发出，语义不变。

### D6：当期数量与统计范围的呈现

`/scope`（日期选择器那条接口）响应增加 `first_listed_count`（当前站点、视图、所选日期的实算数）与 `first_seen_coverage`：`{earliest_known_date, confirmed_since}`。`confirmed_since` = 站点内最早的 `CONFIRMED` 的上线边界——即基线期之后的第一期。

界面：筛选与列用「首次入榜」；说明文案“本期首次进入监测范围的关键词数量”；当所选期次 `<= confirmed_since`（即基线期，所有词都是 `EARLIEST_OBSERVED`）时显示“该期是监测起点，无法判断首次入榜”而不是 0。**0 与“不可判断”必须分开**，与 CLAUDE.md 里“空表格三种成因”是同一类陷阱。

### D7：存量迁移与回填

- **alembic 迁移**（秒级）：`dim_keyword` 加三列（`first_seen_basis` 默认 `'UNKNOWN'`，常量默认值在 PG15 是元数据操作，不重写表）；建 `idx_dim_keyword_first_seen`；删两张事实表的 `is_new`。**必须手写并用 `--sql` 核对 `PARTITION BY` 未丢**（仓库既有纪律，本变更虽不改分区也要核）。
- **`scripts/backfill_first_seen.py`**（一次性，可重复执行）：按站点、按月分区批处理，从 `fact_daily`/`fact_weekly` 取每个 `keyword_id` 的最早 `(date, type)`，用 D3 同一个比较键写入，`basis` 置 `EARLIEST_OBSERVED`（原为 `UNKNOWN` 时）。**永不写 `CONFIRMED`**。输出：按站点的处理数、仍为 `UNKNOWN` 的数、各 basis 计数，并按 `--sample N` 抽样列出（keyword、最早日期、该日事实行是否真存在）供核验。幂等：再跑一遍零更新。
- **上线次序**：迁移 → 部署 → 回填。回填之前导入的新词按 D2 判定，不依赖回填结果；回填只会把更早的证据补进来。回填与导入并发时，二者都只往前修且用同一比较键，结果与顺序无关；脚本取咨询锁与导入互斥是否必要，见 Open Questions。

### D8：证据盘点（实施第一步产出）

动手回填前先盘点并写进变更记录：每个站点 × 周期在库内的最早期次、缺期、`import_batch` 里 `COMPLETED` 的最早批次与当前保留下界。**仅有“最早仍保留的一条记录”不等于历史首次**——这就是为什么回填只产出 `EARLIEST_OBSERVED`。

## Risks / Trade-offs

- **周报来源的首次不进日视图** → 日视图某天的数可能偏少。界面说明承担；备选是把周报首次摊到周内某天，会捏造日期，否决。
- **基线期判定依赖 `min(first_seen_date)`** → 回填未跑之前，站点 `min` 为 NULL，会把“回填前第一次导入”当基线期。可接受：它们只会被记为 `EARLIEST_OBSERVED`（不算新词，偏保守），回填后自然向前修正。反过来偏激进的方向（把存量误算新词）不存在。
- **偏保守的方向是有意的** → 判错时宁可少算首次入榜（基线期、未知一律不算），不多算；多算会让存量整批涌进当期且无报错。
- **`dim_keyword` 现有 1500 万行，新增 btree 的构建耗时/锁** → `CREATE INDEX` 在迁移里会锁写；`dim_keyword` 只有导入第二段写它，选导入空闲窗口执行，迁移里不用 `CONCURRENTLY`（alembic 事务内不允许），实测耗时记进 tasks。
- **删 `is_new` 不可逆** → 回滚靠 `downgrade` 加回列但值全为默认；因为 7 天口径本来就要废弃，接受。
- **词库盯盘共用 `fact_metric_columns`** → 同步改成派生表达式，否则两屏对同一个词显示不同含义（CLAUDE.md 已警示）。

## Migration Plan

1. 迁移（加列/建索引/删 `is_new`）→ 2. 部署后端+前端（同一次发布，前端依赖 `first_listed`）→ 3. 跑 `backfill_first_seen.py --dry-run` 看数量 → 4. 正式回填 → 5. 抽样核验并把数量记入 PR。发布按既有流程，由用户手动触发，不在本变更内发起。回滚：部署回上一版本；迁移 downgrade 加回 `is_new`（全默认）、删三列。

## Open Questions

- **周报文件名日期的语义**（实施任务 1.1 核实）：本设计假设周期 `w` 覆盖 `[w-6, w]`；若真实文件日期是周起始日，则改为 `[w, w+6]`，其余设计不变，只改一个常量与示例。
- 回填脚本是否需要与导入共用咨询锁（`pg_try_advisory_lock`）——两者都只往前修，理论上不必；但 2C4G 上并发全库扫描会抢 IO，倾向取锁，实施时按实测定。
