## Context

市场分析表格的「点击前三 ASIN」（`TopProductCards.tsx`）与「点击前三品牌」
（`RankedList.tsx` via `columns.tsx`）只展示当前查询期次的静态快照。issue #96
要求为这两列各加一个悬浮历史面板：给定表格里当前这一行的某个 ASIN／品牌，回答
「它在**这个关键词**下历史上出现过多少期、各期第几名」。

这是一个新的只读查询能力，与已有的 `keyword-trend-detail`（issue #86，关键词整体
排名走势，`GET /api/keyword/trend`）同属「单关键词历史回溯」这一类查询，但粒度不同：
`keyword-trend-detail` 回答「这个关键词自己的排名怎么变」，本变更回答「这个关键词的
点击前三榜单里，某个具体 ASIN／品牌在哪几期出现过、当时第几名」。两者共用
`analysis_crud.fact_and_datecol` 与 `partitioning.retention_cutoff`，但查询目标、
返回形状、分页需求都不同，不合并成一个端点。

## Goals / Non-Goals

**Goals:**
- 给定 `site + view + keyword_id + on`（来源页面已生效的查询作用域）与一个身份
  （ASIN 原文或品牌原文），返回该身份在这个关键词下、保留窗口内实际进入点击前三的
  期次列表，按日期倒序，支持分页与精确计数。
- 查询先落在 `(site, keyword_id)` 前缀索引上，不扫全站榜单。
- 前端悬浮才发请求、面板内可滚动触底加载、快速切换查看对象时旧响应不覆盖新内容。

**Non-Goals:**
- 不做 ASIN／品牌跨关键词或跨站点的全站上榜统计。
- 不新增数据库表、不新增列、不新迁移——完全基于既有事实表与维度表的只读查询。
- 不做类目历史、商品详情页、独立导航或趋势图。
- 不改变保留策略，不恢复已清理的历史。

## Decisions

### 1. 一个端点服务 ASIN 与品牌两种身份，用 `kind` 判别

`GET /api/analysis/topslot-history?site=&view=&keyword_id=&on=&kind=ASIN|BRAND&value=&page=&page_size=`。

两种身份的响应形状、分页语义、权限规则完全相同，唯一差异是「按哪一组三元列匹配」
（`top{1,2,3}_product_id` vs `top{1,2,3}_brand_id`）与「身份解析走哪张维度表」
（`dim_product.asin` vs `dim_brand.name`）。开两个端点会把这套完全相同的校验/分页/
计数逻辑复制两份——正是 `fact_metric_columns` 那条纪律反复强调的「复制第二份不会
报错，表现是两处行为悄悄分叉」。`kind` 作为一个枚举参数，把分叉点显式收窄到 crud
层一处 if/else（决定用哪三个列 + 哪张维度表），不扩散到 service/api/schema。

**Why not 两个独立端点**：ASIN 与品牌在业务口径上确实是"两份独立的关键词级榜单"
（proposal 已强调），但这条纪律约束的是**数据不能互相推断或配对**，不要求"查询接口
也要分开"。查询接口共享的是分页/计数/权限这套与身份无关的机制，不是业务口径本身。

### 2. 身份解析：先查 `dim_product.asin` / `dim_brand.name` 唯一约束，查不到就返回空结果

`dim_product` 有 `uq_dim_product_asin`，`dim_brand` 有 `uq_dim_brand_name`，两者都是
精确匹配、O(1) 走唯一索引。解析失败（这个 ASIN／品牌从未在系统里出现过）**不报错**，
直接返回 `total=0` 的空结果——因为"这个身份从没在这个关键词出现过"与"解析到了但零期
命中"在业务上是同一种空状态，用户看到的应该都是"没有匹配历史"，不该因为解析路径不同
而一个报 404、一个报空列表。

**Why 不按文本反查事实表**：事实表存的是 `product_id`/`brand_id`，不存 ASIN/品牌
原文；按文本反查意味着每次查询都要多一次跨表 JOIN，且如果将来 ASIN/品牌名允许大小写
或空白差异（未发生，但作为一条纪律）会引入不确定匹配。先精确解析到 id 再查事实表，
是 `analysis_crud._apply_filters` 里 ASIN 筛选一直遵循的路数（等值匹配，不做子串）。

### 3. 槽位匹配与重复占槽用 `LEAST(CASE...)` 取最佳名次，不用 `UNION`/子查询去重

```sql
SELECT date_col,
       LEAST(
         CASE WHEN top1_product_id = :pid THEN 1 END,
         CASE WHEN top2_product_id = :pid THEN 2 END,
         CASE WHEN top3_product_id = :pid THEN 3 END
       ) AS slot_rank
FROM fact
WHERE site = :site AND keyword_id = :kw AND date_col BETWEEN :start AND :end
  AND (top1_product_id = :pid OR top2_product_id = :pid OR top3_product_id = :pid)
ORDER BY date_col DESC
```

`fact_daily`/`fact_weekly` 每个 `(site, keyword_id, 期次)` 恰好一行，这天然保证了
「同一期最多一行」——不需要额外的 `GROUP BY` 或去重逻辑。剩下唯一的不确定性是同一期
里该身份**同时**出现在多个槽位（数据异常场景，正常导入不会产生，但查询侧不能假设
上游永远干净）：`LEAST` 对多个非 NULL 值取最小、忽略 NULL，WHERE 已保证至少一个
非 NULL，因此结果是确定性的「取最佳名次」，与 SQL 引擎的行/列扫描顺序无关。

**Why 不用 `UNION ALL` 三个子查询再在应用层去重**：那样每期最多产出 3 行，去重与
"取最佳"的逻辑要挪到 Python 里做，且 `ORDER BY date DESC LIMIT/OFFSET` 分页在存在
重复行的中间状态下会算错 offset（跨页边界可能把同一期的另一行页面二次纳入或跳过）。
`LEAST(CASE...)` 把这件事收在纯 SQL 里、一期一行，分页可以直接建立在这个结果集上，
不需要应用层二次处理。

### 4. 历史范围：`[retention_cutoff, on]`，不做五态覆盖率枚举

起始日期 = `partitioning.retention_cutoff(today, HOT_WINDOW_MONTHS)`，与
`keyword-trend-detail` 的 `ALL` range 用同一个下界函数。结束日期 = 请求携带的 `on`
（来源页面**已生效**的查询作用域锚定日，不是今天、不是最新一期）。

**与 `keyword-trend-detail` 的关键差异**：那个功能要回答"这段窗口里哪些期次有数据、
哪些没有、哪些超期"，所以要在 `RANKED/DROPPED/NO_DATA/NOT_IMPORTED/OUT_OF_RETENTION`
五态之间枚举每一个期望存在的期次。本变更只要回答"这个身份实际进了前三的是哪几期"，
**不枚举未上榜或未导入的期次**——issue 原文明确「仅展示实际进入前三的期次，不补造
未上榜或未导入记录」。因此这里不需要 `_expected_dates`/`available_dates` 那套机制，
直接按日期范围查询、结果集本身就是"实际命中"的期次，缺失的期次就是不出现在结果里，
不需要额外状态标记。

**Why 仍然要接 `retention_cutoff` 而不是不设下界直接查全表**：不设下界在语义上等价于
"永久累计次数"，与 issue 明确的口径「次数表示这段可用历史中的总期数，不宣称是永久
累计次数」矛盾。即使查询物理上因为分区已被 `DROP` 而不会返回超期数据（不设下界也不
会出错），显式传入 cutoff 能让"总次数的定义"在代码里可读，而不是隐式依赖分区恰好被
清理这件事。

### 5. `on` 必须来自请求参数，不在服务端重新解析"最新一期"

与 `keyword-trend-detail` 的 `_resolve_anchor`（找不到精确匹配就退化到 `<= on` 的
最新一期，`on` 缺省时取全局最新）不同，本端点的 `on` **没有缺省值、不做退化**——
它必须等于来源表格当前已生效查询的那个日期。issue 明确「截止日期取当前表格的查询
日期，不能静默切到今天或最新一期，也不能使用尚未点击查询的草稿条件」。如果服务端
再做一次"取最新一期"的退化，会在下面这个场景下产生静默错误：用户把日期切到三周前
点查询，此时表格显示的是三周前那期的 top1 ASIN；如果历史面板的 `on` 又被服务端悄悄
换成"最新一期"，面板里可能出现比来源表格更新的期次，用户会把它误读成"当前"数据。

因此 `on` 是必填参数，前端从**生效态**的查询作用域（`useAnalysisQuery` 里已提交的
`scope`，不是草稿 `draft`）读取并传入；后端只做"`on` 是否是该 `(site, view)` 下一个
合理日期"的宽松校验（不要求精确命中 `site_data_stats` 的某一行——`on` 允许略早于
`site_data_stats` 里的行，例如保留清理与统计行清理之间的既有缺口，见
`keyword-trend-detail` design 决策 1 同一条纪律），不做退化重算。

### 6. 分页：offset 式 `page/page_size`，`page_size` 上限拒绝不截断，`total` 每页都精确重算

沿用 `import_batch_crud.MAX_PER_PAGE` 的既有范式：`page_size` 有上限（20，面板一屏
大概能看到的行数量级），超限直接拒绝（422）而不是截断——截断会让前端算出的总页数与
实际不符。`total` 用同一个 WHERE 条件单独 `COUNT(*)` 一次，**每次请求都重算**而不是
只在第一页返回：这个查询已经被 `(site, keyword_id)` 收窄到几十/至多几百行（三个月
保留窗口，日视图上限约 90+ 期），重算的代价可忽略，比"只信第一页的 total、后续页
假设不变"更简单也更不容易出现前端状态管理的 bug。

前端侧确保"总次数不随已加载页数变化"：面板只在**首次加载**（打开面板发出第一个
请求）时把 `total` 写入组件状态渲染在标题里，后续翻页请求返回的 `total`
（理论上应该相同）不会覆盖已显示的数字——除非用户关闭再重新打开面板（等于一次新的
首次加载）。这不是"信任第一次请求、忽略后续"的特例逻辑，而是"面板标题只在首次加载
阶段更新"这一条通用状态机规则的自然结果。

### 7. 索引：延用 `idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)`，不新建索引

与 `trend_crud.keyword_history_stmt` 完全一样的索引路径：`(site, keyword_id)` 等值
前缀把扫描范围收窄到这个关键词的全部历史行（三个月保留窗口下约几十到一百多行），
日期范围在这个子集内进一步收窄，槽位身份匹配（`top{1,2,3}_product_id = :pid` 等）
在这个已经很小的行集合上做残余过滤。这条链路里没有任何一步会触达"全站榜单"（8 亿行
事实表的其余部分），满足 issue「通过查询结构／执行计划验证这一点」的要求——验证方式
是 `EXPLAIN ANALYZE` 应显示 `Index Scan`/`Index Only Scan` 命中这条索引，不出现
`Seq Scan`。

不新建按 `product_id`/`brand_id` 的索引：这三个槽位列在事实表上没有索引，本变更的
查询模式是"先按 (site, keyword_id) 收窄到几十行，再在内存里比较三个整数列"，这个
残余过滤的代价与索引扫描相比可忽略，加索引的收益换不回三个整数列索引在 8 亿行写入
路径上的维护成本。

### 8. 权限：`analysis:view`，不复用或新增其它权限点

这个能力完全长在市场分析表格里，读的是当前用户已经能看到的同一份事实数据（没有引入
任何新的私有数据），因此直接复用 `analysis:view`，不像 `keyword-trend-detail` 那样
需要 `require_any_permission`（那是因为它被市场分析与词库两个不同权限域的屏共用）。
本功能只在市场分析屏出现，没有第二个入口，不需要那层放宽。

### 9. 前端竞态防护：请求令牌而非 `AbortController`

面板组件内维护一个自增的请求序号，每次发起请求（首次打开或翻页）前递增并捕获当前值，
响应到达时比对序号是否仍是最新——不是最新就丢弃该响应，不更新任何状态。这与
`window-scoped-rows`/`site-scoped-query` 里"迟到响应不得覆盖新对象"的既有纪律
（`useAnalysisQuery` 已经在用同类模式处理搜索请求的竞态）保持一致的实现风格。

**Why 不用 `AbortController`**：真正 abort 一个已发出的请求在这里没有额外收益
（查询很轻，服务端算完就算完，让它算完但客户端丢弃结果，与真正取消网络请求相比省下的
只是极小的服务端算力），而请求序号的实现更简单、不需要在组件卸载/重开面板的多个时机
都记得调用 `abort()`。

## Risks / Trade-offs

- **[风险] `on` 必填且不退化，若前端某处误传了草稿态而非生效态的日期** → 面板会显示
  与表格当前展示不一致的历史（因为 `on` 变了，`retention_cutoff` 到 `on` 的窗口跟着
  变）。缓解：前端历史面板的数据获取函数只接受来自 `useAnalysisQuery` 生效态 `scope`
  的值，组件测试里加一条"草稿改了但未点查询时打开面板，请求参数不变"的回归。
- **[风险] `LEAST(CASE...)` 依赖三个槽位列都是可空整数且业务上互斥（不会真的重复）**
  → 若未来 fact 模型新增第四个槽位而这条 SQL 没有同步扩展，第四槽位的身份会被这个
  查询忽略且不报错。缓解：crud 层这条查询与 `fact_metric_columns` 的槽位数写在一起
  的注释里互相指向，任何一处扩到 top4 时另一处也要检查。
- **[权衡] `total` 每页都重算而不是只信第一页** → 多一次 `COUNT(*)`，但代价可忽略
  （查询已被收窄到几十行），换来的是不需要在前端额外维护"这是不是第一页"的分支逻辑。
- **[非风险，明确记录] 面板不做超期/未导入的五态展示** → 与 `keyword-trend-detail`
  行为不同是刻意的（决策 4），不是遗漏；两者面向的问题不同，不应被后来者"对齐"成
  同一套展示逻辑。
