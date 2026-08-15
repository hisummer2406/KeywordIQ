## Context

`importer._load_dimensions` 的第二段把三个商品槽位合并去重后写 `dim_product`，冲突时
无条件 `SET title = EXCLUDED.title`。`dim_product` 是 SCD Type 1（不留历史版本），所以
「冲突时更新」本身是对的——错的是**不管有没有变都更新**。

PostgreSQL 的 UPDATE 是写新元组 + 标记旧元组为死，无论新旧值是否相同。因此每条 no-op
更新都要：写一个新行版本、在该行涉及的每个索引上插入新条目、留下一个待 vacuum 的死元组。
`dim_product` 上最大的索引正是被更新列上的 `idx_dim_product_title_trgm`（1389 MB GIN）。

现场证据：`n_tup_upd = 13,889,958` 对 `n_tup_ins = 2,170,554`，死元组 468,675（8.2%）。
一份日报约 40 万行 × 3 个 ASIN 槽位 ≈ 120 万个 ASIN 提及，其中绝大多数早已在库里。

## Goals / Non-Goals

**Goals:**

- 标题未变时不产生行版本，把导入耗时拉回百秒量级
- 消除「维度表越大导入越慢」的正反馈
- 用可观测的判据验证（重复导入的 `n_tup_upd` 增量为 0）

**Non-Goals:**

- 不改 SCD Type 1 语义：标题真变了照样更新
- 不清理存量死元组（另行运维）
- 不动 `dim_keyword` / `dim_brand` / `dim_category`（本来就是 `DO NOTHING`）
- 不动事实表写入路径
- 不重新评估那两个 GIN trgm 索引是否必要（是另一个问题，见 Open Questions）

## Decisions

### D1：加 `WHERE` 到 `DO UPDATE`，不是在应用层预筛

```sql
ON CONFLICT (asin) DO UPDATE
   SET title = EXCLUDED.title
 WHERE dim_product.title IS DISTINCT FROM EXCLUDED.title
```

**否决「先 SELECT 出已存在的再决定插还是更」**：那要把百万级 ASIN 读进应用进程，违反
「不把任何行读进应用进程」的既有约定，内存也不再有界。`DO UPDATE ... WHERE` 全在库内完成。

**为什么是 `IS DISTINCT FROM` 而不是 `<>`**：`title` 虽为 `NOT NULL`，但 `<>` 在任一侧为
NULL 时结果是 NULL（不成立），一旦将来放宽可空性就会静默退化成「NULL 的行永远不更新」。
`IS DISTINCT FROM` 对 NULL 有定义，不留这个坑。

### D2：验证判据是 `n_tup_upd` 增量，不是耗时

耗时受缓存、并发、磁盘状态影响，同一份文件两次跑出的秒数本来就会差不少，用它做断言必然
不稳定。`pg_stat_user_tables.n_tup_upd` 是精确计数：**重复导入相同内容，增量必须是 0**。

耗时仍然要测，但作为人读的观测值（`verify-import` 的输出），不作为自动断言。

### D3：这一条要靠集成测试守，单元测试守不住

判定发生在 PostgreSQL 内部，SQL 字符串比对只能证明「写了这个 WHERE」，证明不了它生效。
回归测试挂在 `TEST_DATABASE_URL` gate 后：建两条相同数据的导入、读 `n_tup_upd` 前后差值。

## Risks / Trade-offs

- **`DO UPDATE ... WHERE` 不满足时该行被跳过，不报错** → 这正是意图；但要确认它不会
  影响 `RETURNING` 或后续步骤对「这一行存在」的假设。当前代码在这段之后不依赖本次 UPSERT
  的返回值，只在第三段按 `asin` JOIN 维度表——行本来就在，跳过更新不影响 JOIN。
- **标题变化的商品仍然产生死元组** → 无法避免，也不该避免；量级从「每次全表」降到
  「真正变化的那部分」。
- **改动效果依赖存量死元组被回收** → 加了 WHERE 之后不再新增 no-op 死元组，但已有的
  46.8 万个要等 autovacuum 或人工 VACUUM。首次实测可能仍偏慢，别据此判定改动无效。
- **假设未做反证实验** → 三条证据（更新/插入比 6.4、被更新列上挂最大索引、耗时随表增长
  恶化）一致指向它，但在 `verify-import` 跑出对照数字之前，这仍是强假设。任务清单里的
  实测就是那个反证。

## 对数据库与容量的影响

- **无 schema 改动，不需要 Alembic 迁移**，`analysis` schema 不变。
- **执行计划**：`ON CONFLICT DO UPDATE` 仍走 `uq_dim_product_asin` 定位冲突行，
  新增的 `WHERE` 只是行级过滤，不改变访问路径。
- **索引维护量下降**：no-op 更新不再触碰 `idx_dim_product_title_trgm`（1389 MB）
  与 `idx_dim_product_asin_trgm`（409 MB），这是耗时下降的主要来源。
- **连接池与内存不变**：仍是同一条导入连接上的库内 `INSERT ... SELECT`，不把行读进应用进程。
- **膨胀速度下降**：死元组不再按「每次导入 × 已存在 ASIN 数」增长，autovacuum 压力随之下降。

## Migration Plan

纯代码改动，随后端发布即可，无迁移、无回滚数据风险。回滚即回退该提交——回退后行为退回
现状（慢，但正确）。

上线后建议做一次 `VACUUM (ANALYZE) analysis.dim_product` 回收存量死元组；这不是本变更的
一部分，但不做的话首次导入仍会偏慢，容易被误读成改动无效。

## Open Questions

- `dim_product` 上两个 GIN trgm 索引（`title` 1389 MB、`asin` 409 MB）合计 1.8 GB，
  是否都必要？`asin` 是精确码，前缀/模糊检索的必要性存疑；若能去掉一个，维度维护成本
  还能再降一截。**本变更不碰**——那要先确认查询侧谁在用它们，是独立的一件事。
