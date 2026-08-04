# KeywordIQ PRD v2 技术规格（Spec）

- 日期：2026-07-10
- 依据：《KeywordIQ PRD v2》（甲方批注已整合版）+ 现有代码库（FastAPI + PostgreSQL 15，schema `analysis`）
- 状态：待评审
- 范围：PRD 第 0～3 阶段已确认的 P0/P1 功能。P2（EXT 01/02/07、PRICE 01）与待定项（eBay、EXT 04 ppspy）不在本 spec 内，仅保留接口预留说明。

---

## 1. 现状与 PRD 的差距评估（本 spec 的架构动因）

| # | 差距 | 影响 |
|---|------|------|
| G1 | 现有数据模型是**单站点**（美国）：`amazon_origin_search_data` 以 `keyword` 全表唯一，无站点维度 | SEL 04 要求美/英/德三站。PRD 问题 13 的答案是：**三站数据不在系统内，需新增接入**，且必须改主表唯一约束与全部查询链路 |
| G2 | 现有模型是**覆盖式 UPSERT**：只保留当前值 + 前值 + 日维度 7 天 JSONB 趋势，周维度无历史 | AMZ 03 新词（昨日新出现/上周新出现）、SEL 07 飙升词、BUG 03 排名曲线都需要**按日期保留的排名历史**。JSONB 数组不可查询、不可回溯，是 BUG 02/03 的疑似根因之一 |
| G3 | FAV 模块（店铺监控）需要 ASIN 级日快照（库存、排名、上架时间、单量），但 **PRD 4.1 数据源表中没有列出 FAV 的数据来源** | FAV 01–07 全部无法排期，属 PRD 遗漏的阻塞项，需向甲方补充确认 |
| G4 | SEL 05 需要 ASIN 主图，PRD 未列图片数据源（PA-API / 爬虫 / 卖家精灵） | 需甲方确认，spec 先按「图片 URL 字段外部提供 + 占位图兜底」设计 |
| G5 | Facebook Ads Library API 官方仅对**欧盟投放广告（DSA 透明度）及政治/社会议题类广告**开放检索；美国站商业广告只能在网页端查看，API 不可用 | EXT 06「输入域名→按推广数量排序」若目标市场是美国，官方 API 大概率不可行，需尽早验证并预研爬虫/第三方广告情报商备选（PRD 风险登记已列，但此处是**可行性问题而非速率问题**） |
| G6 | RISK 01 图形外观检测是图像相似度问题，2C4G RDS + 4C8G ECS 无力承担自建模型 | 上线前必做项，必须走第三方 API 或人工审核兜底路线，不能自建 |
| G7 | Alembic 已在依赖中但从未初始化，现有建表靠 `docs/analysis.sql` | 本期 schema 变更多，必须先建迁移基线 |
| G8 | 周表导入 3M 行已需 30–60 分钟；三站 + 历史表将使写入量约 ×3 | 需要分区表 + 保留策略，否则 RDS 2C4G 撑不住 |

**结论**：PRD 功能清单本身合理，但隐含了一次**数据模型升级**（站点维度 + 排名历史），这是本期真正的技术主线；FAV 数据源缺失是 PRD 层面的新增阻塞项。

---

## 2. 总体架构决策

### D1 保持单体，模块化扩展（选定）
沿用 FastAPI 单体 + 分层模式（model/crud/service/api/schemas）。备选的「拆分独立采集服务」在 4C8G 单机上没有收益，放弃。新增顶层模块：

```
app/
├── table/
│   ├── analysis/      # 既有，扩展 marketplace + 历史查询
│   ├── favorite/      # 新增：店铺监控（FAV）
│   └── risk/          # 新增：风险检测（RISK）
├── integrations/      # 新增：外部数据源适配层
│   ├── base.py        # SourceAdapter 抽象：fetch → normalize → upsert
│   ├── dingtalk/      # 钉钉智能表格（RISK 02a/02b）
│   ├── fb_ads/        # FB Ads Library（EXT 06）
│   └── seller_sprite/ # 卖家精灵（EXT 03，接入方式待确认）
└── jobs/              # 新增：定时同步任务入口（独立进程跑，不占用 API 事件循环）
```

- 适配层统一「字段映射配置化」，直接落实 PRD 风险登记中「钉钉表格结构变化」的应对项。
- 定时任务用系统 cron 调用 `python -m app.jobs.<job>` 独立进程执行，复用现有「worker 进程自建 engine」的守则；不在 uvicorn 进程内跑调度器。

### D2 数据模型：站点维度 + 追加式历史表（选定）

**方案对比**：
- A. 主表加 `marketplace` 列 + 新增追加式 `keyword_ranking_history` 表（**选定**）——改动集中、查询模式清晰、历史可回溯。
- B. 每站一套表——查询/导入代码分叉三份，放弃。
- C. 全面改星型数仓——超出 2C4G 承载与团队规模，放弃。

**主表变更**（Alembic 迁移）：
- `amazon_origin_search_data` 增加 `marketplace VARCHAR(4) NOT NULL DEFAULT 'US'`（枚举 US/UK/DE）。
- 唯一约束由 `(keyword)` 改为 `(keyword, marketplace)`；存量数据回填 `US`。
- 现有索引全部改为带 `marketplace` 前缀的复合索引。

**新表 `keyword_ranking_history`**（追加式，月分区）：

| 列 | 类型 | 说明 |
|----|------|------|
| keyword | varchar(500) | 与主表联查用 |
| marketplace | varchar(4) | US/UK/DE |
| report_type | char(1) | 'D' 日 / 'W' 周 |
| report_date | date | 报告日期，分区键 |
| ranking | int | 当期排名 |
| 唯一约束 | (keyword, marketplace, report_type, report_date) | 导入幂等 |

- 导入流程在现有 UPSERT 之外追加一条 history INSERT（同一事务、同一 minibatch 节奏）。
- **保留策略**：日数据 90 天、周数据 52 周，由月度分区 DROP 实现（jobs 里的 retention 任务）。
- **容量估算**：日 300K×3 站×90 天 ≈ 81M 行 + 周 3M×3 站×52 周 ≈ 47M 行（瘦表，约 15–20GB 含索引）。RDS 2C4G 可承载但已接近上限，上线后监控；若超预期，优先缩短日数据保留期。
- 排名曲线（BUG 03）、新词判定（AMZ 03）、飙升词（SEL 07）全部改由此表查询；`ranking_trend_day` JSONB 保留但停止作为查询依据，稳定一个版本后删除。

**FAV 新表**（数据源确认后启用，表结构先行冻结为接口契约）：
- `favorite_store(id, store_name, store_url, marketplace, created_at)`
- `store_asin_snapshot(asin, store_id, snapshot_date, listing_date, stock, category_rank_week, category_rank_month, est_daily_orders, 唯一 (asin, snapshot_date))`——FAV 01–07 全部筛选都是对该快照表的查询。

**RISK 落地表**：`risk_problem_product`、`risk_infringement_entry`（若甲方确认为同一张钉钉表，则合并为一张 + `entry_type` 列）、`risk_trademark_entry`（RISK 01 检测库，来源待定）。

### D3 迁移基线（前置任务）
初始化 Alembic（`alembic init`，配置 `version_table_schema='analysis'`），首个 revision 以现库结构为基线（stamp），其后所有变更走迁移。这是阶段 0 之后、阶段 1 之前的硬前置。

---

## 3. 模块规格

约定：验收标准沿用 PRD 编号；「默认假设」是待确认项在甲方回复前的实现口径，甲方否决则按新口径改。

### 3.1 阶段 0：缺陷修复（BUG 01–04）

| ID | 修复口径 |
|----|---------|
| BUG 01 | 以原始 CSV 为准逐字段核对导入链路（`csv_processor.py` 字段映射、UPSERT SET 子句），产出比对脚本（抽样 ≥1000 行，误差 0）作为回归测试 |
| BUG 02 | 日变化 = 当日排名 − 前一日排名。疑似根因：UPSERT 时 `previous_rangking_day` 取值时序错误（重复导入同日文件时被覆盖）。修复后按 PRD 人工抽样核对 |
| BUG 03 | 曲线数据源切换至 `keyword_ranking_history`（D2 落地后）；落地前先修 JSONB 数组的排序/截断逻辑。抽样 5 词条核对 |
| BUG 04 | 先用 `pg_stat_statements` + 慢查询日志定位（怀疑：无 marketplace 维度时的全表 count 与 LIKE 过滤）；定位后与甲方确认 P95 目标再修 |

阶段 0 不依赖任何新表，可立即开工。

### 3.2 阶段 1：选品优化（SEL 01–06、09）

| ID | 设计要点 | 验收 / 默认假设 |
|----|---------|----------------|
| SEL 01 | 查询层类目条件改为 `IN (...)` + `NOT IN (...)`，前端多选控件；黑名单类目逻辑保持独立不受影响 | 排除项生效后结果不含被排除类目 |
| SEL 02 | 新增风险词库表 `risk_word(word, translation)`；列表页服务端匹配命中词，返回 `[{word, translation, offset}]`，前端下划线渲染 | **默认假设（问题 9）**：下划线 + hover 原生 title 显示翻译，不做独立弹窗 |
| SEL 03 | 查询默认**排除**匹配 `^B0[A-Z0-9]{8}$` 的词条（`keyword !~ ...`）；去重由 `(keyword, marketplace)` 唯一约束天然保证 | 输出无 ASIN 格式词条、无重复 |
| SEL 04 | 依赖 D2 站点维度。三站 ABA 文件分别上传，上传界面增加站点选择；品牌分析查询增加 `marketplace` 参数，输出格式不变 | 三站数据可分别查询，格式与现有一致。**前置**：甲方提供英/德 ABA 文件样例以确认字段一致性 |
| SEL 05 | 主表增加 `top_product_image_url` 列；**图片来源为 G4 开放问题**，来源确认前该列由后续数据源回填，缺失显示占位图 | 有图显示主图，缺失占位图 |
| SEL 06 | 新端点 `POST /api/analysis/compare`，入参 ASIN/链接列表（≤20，超限 422），输出三类维度对比。流量词分析 = 各 ASIN 关联词条集合对比（基于 `top_product_asin` 等三列反查） | 上限 20；覆盖优势项、流量词分析、销量/流量占比 |
| SEL 07 | 依赖 history 表。**候选公式（按 PRD 4.3 流程提交甲方确认）**：飙升分 = (前期排名 − 当期排名) / 前期排名，日维度窗口 1 天、周维度 1 周；仅计入两期均有排名的词 | 甲方确认公式后固化为验收标准 |
| SEL 08 | **候选公式**：市场份额 = 该词搜索结果总数的倒数加权点击份额（甲方示例以搜索结果数为分母）。搜索结果总数不在 ABA 数据内，**需新增采集渠道，与 G4/G5 一并向甲方确认**；公式草案随阶段 3 提交 | 暂不可验收（PRD 原文） |
| SEL 09 | 日新词（AMZ 03 产出）关联展示 FB 广告库链接与独立站推广链接；词条点击跳转 `https://www.amazon.{tld}/s?k={keyword}` | 联调依赖 EXT 06；EXT 06 未通前仅展示 Amazon 跳转 |

### 3.3 阶段 1：店铺监控（FAV 01–07）——**数据源阻塞，表结构先行**

**阻塞声明**：ASIN 快照数据（上架时间、库存、排名、单量）来源未在 PRD 中定义（G3）。本节以 `store_asin_snapshot` 表为契约完成查询侧设计与实现，数据接入侧待甲方确认来源后另行排期。

| ID | 查询口径（对 snapshot 表） |
|----|---------------------------|
| FAV 01 | `now() - listing_date <= 3×24h` |
| FAV 02 | 同上 5 天，且 `category_rank_week IS NOT NULL` |
| FAV 03 | 当前 `stock` ≠ 首条快照 `stock` |
| FAV 04 | 当期 `category_rank_week/month` < 上一周期同口径值 |
| FAV 05 | 连续 N 天 `est_daily_orders > 5`，N 配置项默认 3（问题待确认） |
| FAV 06 | 近 7 天日均单量 ≥ 前 7 天 ×2（PRD 草拟阈值，配置项，待确认） |
| FAV 07 | **默认假设（问题 8）**：实现为 FAV 01/02 结果上的布尔列「已出单」，同时提供筛选参数——两种口径一次覆盖 |

**日单量默认估算**（供甲方确认）：`est_daily_orders = max(0, 前日库存 − 当日库存)`，补货日（库存上升）记 0 并打补货标记。

### 3.4 阶段 1：平台数据分析（AMZ 01–03）

| ID | 设计要点 |
|----|---------|
| AMZ 01 | 上周排名为 0/空 且本周排名非空，按本周排名升序取前 25。基于主表周字段即可 |
| AMZ 02 | 两周数据均存在的词条中筛选仅本周新增（`is_new_week` 口径修正为基于 history 表判定） |
| AMZ 03 | 基于 history 表：日维度 = 昨日首次出现且今日仍有排名；周维度 = 上周首次出现且本周仍有排名；均按当期排名升序。**默认假设（问题 11）**：「新词」= history 表中该 `(keyword, marketplace, report_type)` 的**首次出现**（非 Top N 口径）；若甲方选 Top N，仅改 WHERE 条件，不动表结构 |

### 3.5 阶段 2–3：风险检测（RISK 01/02/03）

| ID | 设计要点 |
|----|---------|
| RISK 02a/02b | 钉钉智能表格 API 同步任务（`app/jobs/sync_dingtalk.py`，cron 每日），字段映射写在配置中。词条匹配为导入后批量任务 + 查询时标签展示。**默认假设（问题 7）**：按两张表设计（合并比拆分改造成本低，PRD 风险登记同向） |
| RISK 01 | 上线前必做。**文字商标**：对接检测库做词面匹配（库来源待甲方，问题 4）；**图形外观**：受 G6 约束，方案限定为「第三方图像检索 API + 命中后人工确认」，不自建模型。系统输出一律标注「风险提示，非法律意见」（落实 PRD 风险登记第 1 条），前端固定免责声明 |
| RISK 03 | 词条列表勾选 → 基于 history 表输出排名趋势曲线（复用 BUG 03 的查询）。**候选算法**：热度分 = 归一化排名倒数的 7/30 天移动均线，随阶段 3 提交甲方 |

### 3.6 阶段 2：站外渠道（EXT 03、06）

| ID | 设计要点 |
|----|---------|
| EXT 06 | `app/integrations/fb_ads/` 适配器：输入域名 → 检索广告 → 按推广数量排序取最多推广产品、日期取最新，结果落 `fb_ads_domain_stat` 表供 SEL 09 消费。**首要任务是 G5 可行性验证（1–2 天 spike）**：确认目标市场（若为美国，官方 API 不覆盖商业广告，直接转爬虫/第三方备选评估）；产出验证报告后再实现 |
| EXT 03 | 谷歌趋势相关推荐词复用卖家精灵，API/插件方式待确认（问题）。适配器占位，接口签名冻结：`fetch_related_terms(keyword, marketplace) -> list[RelatedTerm]` |

### 3.7 P2 预留
EXT 01/02/07、EXT 04（ppspy）、PRICE 01–03、eBay：仅在 `integrations/base.py` 的适配器抽象上预留注册点，不做任何实现。PRICE 02 的 Excel 解析在甲方提供模板后按独立 spec 处理。

---

## 4. 非功能需求

- **性能**：查询接口 P95 目标随 BUG 04 排查后与甲方确定；导入不得阻塞 API（沿用分片 + 多进程 + minibatch 提交；`MAX_WORKERS ≤ 3`）。
- **容量**：history 表月分区 + 保留策略（日 90 天/周 52 周）；连接数守则不变（池总量远低于 RDS 400）。三站周表导入总时长预估 1.5–3 小时，需与甲方确认导入窗口。
- **合规/安全**：商标检测免责声明；FB/钉钉的 token 全部走 `.env`（同步更新 `.env.example`）；外部适配器全部带速率限制与重试退避。
- **可观测**：每个 jobs 任务写入 `import_task` 风格的任务记录表（复用 `import_model.py` 模式），失败可查。

## 5. 测试策略

- 单测不依赖真实 PG（守则）；导入/迁移测试用小 fixture CSV。
- 阶段 0 每个 BUG 修复配一个回归测试（守则要求）。
- 新词/飙升/FAV 筛选均为纯查询逻辑，用内存 SQLite 不可行（用了 PG 方言/分区），改为「查询构造单测 + 可选的本地 PG 集成测试」两层。
- 外部适配器一律 mock HTTP 层；钉钉/FB 各留一个真实冒烟脚本（不进 CI）。

## 6. 交付阶段映射（与 PRD 第 7 节对齐）

| 阶段 | 内容 | 本 spec 新增前置 |
|------|------|-----------------|
| 0 | BUG 01–04 | 无 |
| 0.5 | **Alembic 基线 + marketplace 迁移 + history 表**（新增） | D2/D3 |
| 1 | SEL 01–06/09、FAV 查询侧、AMZ 01–03 | FAV 数据源确认（G3） |
| 2 | 钉钉对接（RISK 02a/b）、EXT 06（先 G5 spike）→ 解锁 SEL 09 联调 | G5 验证报告 |
| 3 | SEL 07/08、RISK 03 公式确认后补齐；RISK 01 | 问题 4、G6 路线确认 |

## 7. 开放问题（PRD 14 项之外本 spec 新增）

| # | 问题 | 责任方 | 阻塞 |
|---|------|--------|------|
| 15 | FAV 模块 ASIN 快照数据源（库存/排名/上架时间来自哪里） | 甲方+开发方 | FAV 全模块数据侧 |
| 16 | SEL 05 ASIN 主图来源（PA-API / 爬虫 / 卖家精灵） | 甲方+开发方 | SEL 05 |
| 17 | EXT 06 目标市场（美国 or 欧盟）——决定 FB 官方 API 是否可行 | 甲方 | EXT 06、SEL 09 联调 |
| 18 | SEL 08 「关键词搜索结果总数」采集渠道（不在 ABA 数据内） | 开发方 | SEL 08 |
| 19 | 英/德站 ABA 文件格式样例 | 甲方 | SEL 04 |
| 20 | 三站导入窗口与 history 保留期确认 | 甲方 | 容量规划 |

各「默认假设」若甲方在阶段排期前未否决，即按本 spec 口径实现（与 PRD 风险登记「超期按候选方案默认上线」一致）。
