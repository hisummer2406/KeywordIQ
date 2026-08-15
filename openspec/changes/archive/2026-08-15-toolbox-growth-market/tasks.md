## 1. 数据模型与迁移

- [x] 1.1 在 `app/table/analysis/fact_model.py` 加 `FactGrowth` 模型（`site` / `anchor_date` /
      `keyword_id` 复合主键，`start_ranking` / `end_ranking` / `rank_gain` /
      `min_growth_rate` / `created_at`），带列注释与 `postgresql_partition_by`
- [x] 1.2 登记 `partitioning.PARTITIONED_TABLES`（`anchor_date`，`site_subpartition=False`）
      与 `migration_scope.V2_TABLES`
- [x] 1.3 手写 Alembic 迁移：建分区父表 + `idx_fact_growth_rank`，并种 `toolbox:view`
      权限点与三个内置角色的关联；写降级
- [x] 1.4 `uv run alembic upgrade head --sql` 核对 DDL 里确有 `PARTITION BY RANGE (anchor_date)`，
      再对 `keywordiq_test` 实跑 `upgrade head` 与一次 `downgrade -1`

## 2. 配置与权限点

- [x] 2.1 `config.py` 加 `GROWTH_WINDOW_DAYS` / `GROWTH_MIN_RANK_GAIN` /
      `GROWTH_MIN_DAILY_RATE`（含校验：窗口 ≥2、净增 >0、环比 0~1）
- [x] 2.2 同步 `.env.example`（含单位、默认值、以及「改阈值需回填」这条注释）
- [x] 2.3 `app/auth/permissions.py` 加 `TOOLBOX_VIEW`，写进 `PERMISSIONS` 与
      admin / operator / analyst 三个角色

## 3. 导入侧算定

- [x] 3.1 `importer.py` 加 `_load_growth()`：窗口函数 SQL，仅 `period == 'DAY'` 执行；
      先删该 (站点, 锚定日) 切片再写
- [x] 3.2 `import_report` 里在 `_load_dropped` 之后调用；写前 `ensure_fact_partitions(
      session, "fact_growth", site, report_date)`；`ImportResult` 加 `growth_rows`
- [x] 3.3 集成测试 `test/test_growth_calc_integration.py`：命中、单日环比 9.9% 出局、
      净增 9999 出局、中途掉榜出局、窗口缺一期整期为空、同期重导幂等、WEEK 不写入。
      计数一律带 `WHERE site=... AND anchor_date=...` 作用域

## 4. 查询层

- [x] 4.1 新建 `app/table/toolbox/`：`growth_schemas.py`（请求/响应 DTO，Pydantic v2）
- [x] 4.2 `growth_crud.py`：分页查询 + `count(*)` + 当页批量补 7 点走势（纯 SQL 组装）
- [x] 4.3 `growth_service.py`：作用域解析（站点校验、锚定日校验、缺期计算）、排序白名单
- [x] 4.4 `growth_api.py`：`POST /growth/search`、`GET /growth/scope`，均挂
      `require_permission("toolbox:view")`；`main.py` 挂载 `/api/toolbox`
- [x] 4.5 单元测试：排序白名单拒绝非法字段、缺期计算、非法站点报错不回退
- [x] 4.6 集成测试：ProTable 契约形状、走势点数恰为 7、无 token 返回 401（进
      `test_protable_contract.py`）

## 5. 回填脚本

- [x] 5.1 `scripts/backfill_growth.py`：`--site` / `--from` / `--to` / `--dry-run`，
      逐期算定，跳过窗口不完整的期次并报告
- [x] 5.2 与导入侧共用同一份算定函数（口径只能有一处定义），脚本自建会话与引擎
- [x] 5.3 单元测试：期次枚举与「窗口不完整则跳过」的判定

## 6. 前端

- [x] 6.1 `.umirc.ts` 加「工具箱」父子路由（`/toolbox` → `/toolbox/growth` 重定向）；
      `access.ts` 加 `canViewToolbox`
- [x] 6.2 `services/toolbox.ts`：两个端点的请求封装 + 类型
- [x] 6.3 `pages/Toolbox/GrowthMarket/`：作用域栏（站点 + 日期 + 立即查询，draft/applied
      两份状态）、口径说明条（数值来自 `/scope`）、缺期提示、ProTable + 走势小图
- [x] 6.4 `pages/Toolbox/DesignPatent/`、`pages/Toolbox/Trademark/`：占位屏，不发请求
- [x] 6.5 vitest：作用域栏纯函数、缺期文案、口径文案取自服务端值

## 7. 文档与收尾

- [x] 7.1 `CLAUDE.md` 补一段工具箱与增长榜（算定位置、阈值、改阈值要回填、周报不参与）
- [x] 7.2 `uv run pytest test/ -q` 全绿（含集成测试，需 `TEST_DATABASE_URL`）；
      `frontend` 侧 `pnpm lint && pnpm tsc && pnpm test` 全绿
- [ ] 7.3 按路径提交、推分支、`gh pr create --base v2`，PR 正文含变更 id、`Refs #10`、
      spec delta 文件列表 —— **收工动作本身，故在本次提交里仍是未勾选状态**

---

2026-08-15 一次会话内完成全部 7 组。验证记录：

- `uv run alembic upgrade head --sql` 确认 `fact_growth` 的 DDL 含 `PARTITION BY RANGE (anchor_date)`；
  对 `keywordiq_test` 实跑 upgrade → downgrade -1 → upgrade，`alembic check` 报「No new upgrade operations detected」
- `uv run ruff check .` 通过；`TEST_DATABASE_URL=... uv run pytest test/ -q` → **505 passed, 1 skipped**
  （新增 8 + 10 + 11 + 6 + 2 个用例，另更新了 RBAC 矩阵与分区注册表两处既有断言）
- `scripts/backfill_growth.py` 在测试库上端到端跑过：dry-run 报 1 行且库内确为 0 行，
  实跑后落库 1 行，随后清理干净
- 前端 `pnpm lint` / `pnpm tsc` / `pnpm test` 全绿（**181 passed**，新增 17 个）

未做（有意留给后续变更）：外观专利、全球商标库的数据源接入——本变更只占菜单位置。
