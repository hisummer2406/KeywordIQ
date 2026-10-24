## Context

现状：`detectSearchKind` 以 `^B0[A-Z0-9]{8}$` 判定检索框输入，命中走 `asin`（单值，等值匹配三个槽位），否则走 `keyword` + `keyword_mode`。模式下拉作为输入框 suffix，ASIN 输入时隐藏。#146 刚修过该下拉「点开即收起」（Input 外壳 onClick 抢焦点）。

## Goals / Non-Goals

**Goals:** 显式三选一的搜索方式；最多 100 个 ASIN 的批量任意命中；列表、总数、翻页、导出同一口径；三站点、日/周均可用。

**Non-Goals:** 多行常驻输入区；按 ASIN 前缀或格式校验；把搜索方式写进「保存的条件」；ASIN 片段匹配。

## Decisions

### D1 搜索方式是草稿状态，取代格式推断

`FilterDraft.qMode` 的类型从 `KeywordMode` 扩为 `SearchMode = 'ALL_TERMS' | 'PHRASE' | 'ASIN'`，`toSearchBody` 只按它路由：

- `ASIN` 发 `asins`。
- 其余发 `keyword` + `keyword_mode`。
- 两者互斥，切换方式后旧目标不会残留。

删除 `detectSearchKind`。

备选方案是 `qTarget` + `qMode` 两个字段，被否决：会出现「目标=ASIN、模式=PHRASE」这种无意义组合，界面上也只有一个下拉，状态应与之一一对应。

沿用 `qMode` 字段名，是为了让 `submitScope` / `resetFilters` / 保存条件剥离等既有路径不改调用形状。字段的含义扩大了，所以会话快照提升 `CACHE_VERSION`：旧快照中 `q='B0…'` + `qMode='ALL_TERMS'` 在新语义下会被当成关键词查询，与用户当初看到的不一致，直接丢弃。

### D2 接口字段 `asins: list[str]`

与 `brands` / `categories` 的集合形状一致，不再在字符串里约定分隔符。

请求模型的校验逐项处理：

- 去首尾空白、转大写、丢弃空项、保序去重。
- 去重后多于 100 个时报 422「最多支持 100 个 ASIN」。
- 空集合视同不筛（`None`），保证 `request_has_filters` 不会把空列表计为带筛选。

不在原始列表上设 `max_length=100`，因为重复项应先去重再计数（issue 要求「去重后最多 100」）。前端已经去重，后端不依赖这一点。

拆分分隔符只在前端做：后端接收的已经是列表，再按空白拆分会让同一份口径存在两处。

### D3 SQL：先解析 product_id 集合，再三槽位 IN

```sql
top1_product_id IN (SELECT product_id FROM dim_product WHERE asin IN (...))
OR top2_product_id IN (...) OR top3_product_id IN (...)
```

子查询走 `uq_dim_product_asin`，最多 100 个点查。三个槽位列没有索引，查询仍是对当期 `(站点, 日期)` 分区的扫描，与现行单个 ASIN、`competitor_new` 的形状相同。

一个关键词在事实表中只有一行，WHERE 子句不会产生重复行：同一关键词命中多个槽位或多个 ASIN 仍只出现一次，总数按行计。

未收录的 ASIN 在子查询中不产生 product_id，不影响其余 ASIN 的命中。

### D4 单行输入与粘贴

`<input type=text>` 在粘贴时会剥掉换行，导致 Excel 多行粘贴被粘成一个长串。处理方式：在 ASIN 方式下拦截 `onPaste`，把 `\r\n` / `\n` / `\t` 转成空格后插入光标处。

关键词方式不拦截，保持原生行为。

### D5 一行布局与下拉可用性

搜索方式下拉是 `Space.Compact` 中位于输入框之前的独立 `Select`，与站点、视图、期次同高（38px），不再嵌在 Input 的 suffix 里。这样 #146 的冒泡拦截也就不再需要。

下拉宽度用 `popupMatchSelectWidth={false}`，避免选项文字被截断。

ASIN 方式下，输入框 suffix 显示已识别的个数（去重后）。超过 100 时以错误色提示，且「立即查询」与回车都不提交。

## Risks / Trade-offs

- **槽位列无索引：** 100 个 ASIN 与 1 个 ASIN 的扫描量相同（都是整个当期分区），代价不随个数线性增长。需在大站点日报规模上用 EXPLAIN 确认计划没有退化成嵌套循环。
- **旧页面兼容：** 旧页面发送的 `asin` 被静默忽略，会得到未筛选的结果。前后端同镜像发布，刷新即可恢复，接受这个代价。
