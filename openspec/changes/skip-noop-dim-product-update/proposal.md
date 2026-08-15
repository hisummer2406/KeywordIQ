## Why

同等体量的日报，导入耗时在两周内从约 100 秒涨到约 900 秒，且仍在恶化：

| 期次 | 行数 | 耗时 | 导入日期 |
|---|---|---|---|
| 2026-08-08 | 410,425 | **97s** | 08-12 |
| 2026-08-04 | 405,270 | **935s** | 08-16 |
| 2026-08-05 | 100,990 | 54s → **416s** | 08-12 → 08-16（同一份文件重导） |

同一份文件、同一台机器，慢了 7.7 倍。根因在 `importer.py` 的商品维度 UPSERT：

```sql
ON CONFLICT (asin) DO UPDATE SET title = EXCLUDED.title   -- 无条件
```

已存在的 ASIN **每次导入都被重写一遍**，哪怕标题一字未变。每条 no-op UPDATE 都要造一个
死元组、维护 GIN trgm 索引、更新唯一索引与主键。`pg_stat_user_tables` 的证据：
`n_tup_upd = 13,889,958` 是 `n_tup_ins = 2,170,554` 的 **6.4 倍**，而被更新的 `title` 列上
正挂着最大的那个索引（`idx_dim_product_title_trgm`，1389 MB）。

**它会自我加速恶化**：`dim_product` 越大，新导入里「已存在」的比例越高，白做的 UPDATE 越多，
承受这些更新的索引也越大。这是正反馈，不是一次性的慢。

按生产规模（约 6600 万行/月、4C8G ECS + 2C4G RDS）推下去，周报导入会冲出可接受窗口。

详见 issue #33。**本变更影响日报与周报两条导入路径**——两者共用同一段维度补齐代码。

## What Changes

- 商品维度 UPSERT 增加 `WHERE dim_product.title IS DISTINCT FROM EXCLUDED.title`，
  标题未变时**不产生新行版本**——不造死元组、不动 GIN 索引。
- 补一条回归测试：同一批数据连导两次，第二次的 `n_tup_upd` 增量为 0。
- **不改** `dim_keyword` / `dim_brand` / `dim_category`：它们用的是 `DO NOTHING` +
  `NOT EXISTS` 预筛，本来就不会重写既有行。
- **不做**存量死元组的清理（约 46.8 万行、8.2%）。那是一次性运维动作（`VACUUM`
  / `REINDEX`），与代码改动的生效条件不同，混在一起会让「改动是否有效」无从判断。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `site-scoped-import`: 新增一条要求——维度补齐 SHALL 只在属性真正变化时才写入，
  重复导入相同内容 MUST NOT 产生新的行版本。这是导入管线的行为契约（与「只追加不原地
  更新」同类），故落在这份 spec。

## Impact

- **代码**：`app/table/upload/importer.py` 的 `_load_dimensions`（一处 SQL）。
- **数据库**：无 schema 改动，**不需要 Alembic 迁移**；`analysis` schema 不变。
- **接口 / 配置**：无改动，`.env.example` 无需同步。
- **性能**：预期把日报导入拉回百秒量级，且不再随 `dim_product` 增长而恶化。
  连接池占用不变（仍是同一条导入连接），内存占用不变（仍是库内 `INSERT ... SELECT`）。
- **行为不变的部分**：标题**确实变了**的商品仍然照常更新（SCD Type 1 语义不动）。
- **已知不覆盖**：存量死元组的回收另行运维处理；`fact_daily` / `fact_weekly` 的写入路径不动。
