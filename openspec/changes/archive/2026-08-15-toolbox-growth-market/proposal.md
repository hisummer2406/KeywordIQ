## Why

选品主线目前只回答「这一期谁在榜、排名怎么变」，回答不了「哪些词正在**持续**上升」——
后者要跨 7 期看每一天的节奏，用户今天只能把 7 天的导出摞在一起人肉比对。需求方给的口径
（近 7 天名次累计前进超过 1 万名，且每一天的前进幅度都 ≥10%）是固定的，正好落在
「导入时算定、查询侧只读」这个已经用在掉榜集合上的模式里。

同时要给这类「不属于选品主线的独立工具」开一个菜单容器。「外观专利」「全球商标库」
的数据源尚未确定，本变更**只占住菜单位置**，不实现——三件事性质不同，混在一起做会被
数据源采购这类外部条件拖住，连带把已经能做的持续增长市场一起卡死。

**影响范围：只影响日报路径。** 周报没有「连续 7 天」这个概念，`period='WEEK'` 的导入
不触发算定、不写榜单表。

## What Changes

- 新增算定表 `analysis.fact_growth`：记录每个 (站点, 锚定日) 命中持续增长口径的关键词
  及其起止排名、累计净增名次、窗口内最低日增长率。按 `anchor_date` 月度 RANGE 分区、
  不做站点子分区，登记进 `PARTITIONED_TABLES` 与 `V2_TABLES` 后随保留窗口一同移出。
- 日报导入收尾新增第五段 `_load_growth`：在事实与掉榜写入之后、同一事务内算定本期榜单。
  同期重导先删该 (站点, 锚定日) 切片再重算，与既有覆盖语义一致。
- 新增查询能力与端点 `POST /api/toolbox/growth/search`、`GET /api/toolbox/growth/scope`，
  ProTable 契约，权限点 `toolbox:view`。
- 新增权限点 `toolbox:view`，授予 admin / operator / analyst 三个内置角色。
- 前端新增「工具箱」一级菜单与三个二级屏：持续增长市场（完整实现）、外观专利与全球
  商标库（占位页，不发请求、无后端端点）。
- 新增回填脚本 `scripts/backfill_growth.py`：对保留窗口内已导入的日报期次逐期算定。
  没有它，上线当天三个月的历史数据一条榜单都没有。
- 新增配置 `GROWTH_WINDOW_DAYS` / `GROWTH_MIN_RANK_GAIN` / `GROWTH_MIN_DAILY_RATE`。

无 BREAKING：既有表、端点、查询路径均不改动。

## Capabilities

### New Capabilities

- `growth-market-list`：持续增长市场榜单——口径定义、导入时算定、查询与分页、缺期语义。
- `toolbox-navigation`：工具箱菜单容器与权限点，含未接入数据源的占位屏应有的行为。

### Modified Capabilities

无。`site-scoped-import` 描述的是 ABA 报表的三段式装载，本变更新增的是它之后的一段
收尾算定，不改变已有要求；`site-scoped-query` / `window-scoped-rows` 管的是分析屏的
作用域，工具箱是另一条查询路径，不共用作用域规则。

## Impact

| 层 | 影响 |
|---|---|
| 数据模型 | 新表 `fact_growth`（`app/table/analysis/fact_model.py`）+ 手写 Alembic 迁移；`partitioning.PARTITIONED_TABLES`、`migration_scope.V2_TABLES` 各加一项 |
| 导入 | `app/table/upload/importer.py` 新增 `_load_growth`，`ImportResult` 增字段；**仅 DAY 路径** |
| 查询 | 新模块 `app/table/toolbox/`（crud/service/api/schemas），`main.py` 挂载路由 |
| 鉴权 | `app/auth/permissions.py` 加 `toolbox:view`，迁移种子同步写入 |
| 前端 | `.umirc.ts` 加父子路由、`access.ts` 加 `canViewToolbox`、`pages/Toolbox/` 三屏、`services/toolbox.ts` |
| 运维 | `scripts/backfill_growth.py`；保留清理与分区创建自动接管，无需改动 |
| 配置 | 三个 `GROWTH_*` 项，同步 `.env.example` 与 `CLAUDE.md` |
