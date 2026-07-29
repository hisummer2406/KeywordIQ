## 1. 配置

- [x] 1.1 复用既有 `config.py` 的 `HOT_WINDOW_MONTHS`（语义即保留月数），默认由 12 改为 3（原计划新增 `DATA_RETENTION_MONTHS`，因发现既有配置而改为复用）
- [x] 1.2 在 `.env.example` 同步 `HOT_WINDOW_MONTHS=3` 及注释（默认 3 = 当前月 + 前两个月）

## 2. 分区移出能力（partitioning 层）

- [x] 2.1 实现按 PostgreSQL 目录（`pg_inherits`/`pg_class` + `pg_get_expr(relpartbound)`）枚举月度分区及边界的 `list_month_partitions`
- [x] 2.2 实现 `retention_cutoff(today, retention_months)`（配套纯函数 `subtract_months`）：计算保留窗口下界
- [x] 2.3 实现移出单个超期月度分区的 `drop_month_partition`（`DROP TABLE IF EXISTS`，连带站点子分区；不 commit）
- [x] 2.4 实现 `drop_partitions_older_than(session, table_name, cutoff)`：枚举 + 逐个移出，返回被移出分区；纯选择逻辑抽成可单测的 `select_expired_partitions`

## 3. 保留清理编排（service 层）

- [x] 3.1 新增 `retention_service.run_retention`：对 `fact_daily` 与 `fact_weekly` 统一按同一 cutoff 移出超期分区，service 层控制事务
- [x] 3.2 `export_partition_to_csv`：移出前用服务端 `COPY` 流式导出该月份数据留档（`export_dir` 开启时）
- [x] 3.3 支持 `dry_run`：只枚举并返回将移出的分区，不导出、不执行 DDL、不提交

## 4. 调度入口

- [x] 4.1 新增 `scripts/prune_retention.py`，支持 `--dry-run` / `--retention-months` / `--export-dir` / `--yes`，自建并释放 session
- [x] 4.2 在脚本 docstring 给出 cron / 容器定时任务接入方式（低峰触发）

## 5. 测试

- [x] 5.1 `test/test_retention_unit.py`：`subtract_months` / `retention_cutoff` 的月份边界（跨年、边界月数、忽略日），不依赖真实 PostgreSQL
- [x] 5.2 `test/test_retention_unit.py`：`select_expired_partitions` 的超期判定（`end<=cutoff` 边界、窗口内不选、排序）
- [x] 5.3 `test/test_retention_integration.py`（`TEST_DATABASE_URL` 守卫）：写入跨月数据 → 清理 → 断言超期分区已移出、站点子分区连带删除、窗口内数据仍可查、其他月份不受影响

## 6. 文档与容量修订

- [x] 6.1 重写 `CLAUDE.md` 保留策略与容量段落：只保留三个月、自动移出；满窗约 2 亿行 / 50 GB，通常无需扩容
- [x] 6.2 在 `CLAUDE.md` 保留段落显著标注 BREAKING：超过三个月的历史数据不再可在线查询

## 7. 存量迁移

- [x] 7.1 存量一次性清理复用 `scripts/prune_retention.py`（移出所有超期月份，支持 `--dry-run` 核对与 `--export-dir` 留档），已在脚本 docstring 说明该用途
