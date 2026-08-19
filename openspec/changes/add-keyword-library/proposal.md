## Why

分析查询屏回答的是「今天这个站点有哪些词」，它是一次性的：查完、看完、离开，下次再查
还得把条件重新摆一遍。用户真正长期关心的往往只是其中几十个词——自己在推的那批词、
竞品的那批词——他要的不是每天重新筛一遍，而是**打开就看到这批词今天排到第几、掉没掉**。

仓库目前没有任何「用户自建清单」的概念：`dim_keyword` 是导入产物，谁都不能往里加东西，
也没有任何表把「某个用户关心哪些词」记下来。来源 issue #37 第 3 项。

**影响路径**：日报与周报**两者**。盯盘屏的视图切换与分析屏同构（天 / 周），底层分别读
`fact_daily` 与 `fact_weekly`；导入路径完全不受影响，本变更不写事实表、不改导入。

## What Changes

- **新增两张表** `keyword_group`（分组）与 `keyword_group_item`（条目），落在 `analysis`
  schema，普通表、不分区、不参与保留清理。
- **分组按站点归属**：建组时选定站点，此后不可改；组内只能装该站点的词。这与
  `dim_keyword` 的 `(site, keyword)` 唯一形状一致，盯盘屏选中分组即确定站点。
- **分组私有**：每个用户只看得到自己的分组，可见性由查询条件里的 `user_id` 保证，
  不依赖前端不显示。
- **新增工具箱二级屏「我的词库」**（`/toolbox/library`），左侧分组管理、右侧盯盘表格。
  沿用 `toolbox:view`，**不新增权限点**。
- **盯盘的行集合由分组条目决定**，事实表只用于补数——这与分析查询屏（行集合 = 在榜 ∪ 掉榜，
  由事实表决定）相反。盯的词掉榜或某期无数据时，行仍在，状态明示。
- **分析查询屏结果表格新增多选**，选中若干行后可「加入词库」，选择同站点的分组（可现场新建）。
  这是**唯一**的入库入口，词库屏不提供手工录入。
- **新增端点** `/api/toolbox/library/*`（分组增删改查、条目批量增删、盯盘查询、可选日期集），
  全部挂 `toolbox:view` 且按当前用户过滤。

## Capabilities

### New Capabilities

- `keyword-library`: 用户自建关键词清单的数据模型（分组按用户 + 站点归属、条目按
  `keyword_id`）、规模上限、盯盘查询的行集合语义与状态口径、端点与私有性保证。

### Modified Capabilities

- `toolbox-navigation`: 工具箱下由三个二级项变为四个，新增「我的词库」，仍共用
  `toolbox:view` 这一个权限点。
- `analysis-screen-layout`: 结果表格新增行多选与「加入词库」入口；选中状态不进会话快照。

## Impact

- **新增代码**：`app/table/toolbox/library_{model,crud,service,api,schemas}.py`；
  前端 `frontend/src/pages/Toolbox/KeywordLibrary/`。
- **改动代码**：`app/table/analysis/analysis_crud.py`（把事实行的列投影抽成共用函数，
  供词库盯盘复用，不复制第二份）；`frontend/src/pages/Analysis/index.tsx`（行多选 +
  批量操作入口）；`frontend/config/routes.ts`（新增二级菜单）；`migration_scope.py`
  （登记两张新表）。
- **数据库**：一条 Alembic 迁移建两张普通表（可 autogenerate，非分区对象）。
- **不影响**：导入路径、事实表与维度表的写入、保留清理、既有查询与导出的行为。
