## Why

市场分析与我的词库已经有近 7 天 / 4 周的排名小图，适合扫词，但缺少「选中一个词，看它完整的
在线历史」这一步。同样从 10 万名升到 2 万名，可能是持续走强，也可能是长期高位词下跌后的
回升——小图的固定窗口回答不了这个问题，用户现在只能靠导出多份 CSV 自己拼。

参考卖家精灵的排名趋势交互，第一版只做「现有在线历史内的关键词 ABA 排名详情」，不做搜索量、
展示量、月排名、跨年对比——事实表里没有这些字段，估算或换算出来的数字会被当成真实数据用。

见 GitHub issue #86（用户已确认启动实现，范围与口径已在 issue 中逐条落定）。

## What Changes

- **新增关键词趋势详情**：抽屉形式，从市场分析与我的词库两个入口共用同一个组件打开，
  不新增导航菜单。抽屉里可切换日 / 周视图（各自查询对应事实表，不混用不拼接）与
  「近 30 天」/「全部可用历史」两种范围。
- **新增 `GET /api/keyword/trend` 端点**：给定 `keyword_id` + `site` + `view` + `range`，
  按真实日期返回一段时间线，每个期次标注五种状态之一（在榜有排名 / 掉榜 / 报表已导入但
  未收录该词 / 该期报表未导入 / 已超出保留范围），并给出区间最好排名、最近可用排名及其
  日期、实际数据覆盖范围。查询只按单个 keyword_id 的历史索引取数，不扫全站榜单。
- **权限点复用而非新增**：接受 `analysis:view` 或 `keyword:view` 任一即可访问（两个入口
  各自持有其中一个权限点的用户都必须能用），同时校验 `keyword_id` 与请求的 `site` 一致，
  防止跨站点 id 被拿来查询另一个站点的历史。
- **前端新增 `KeywordTrendDrawer` 组件**：真实时间轴绘图（不用等距点、不补 0、不跨缺口
  连线），缺期原因按上述五态分别提示；提供「加入词库」操作，复用现有分组/站点/配额约束，
  从词库入口打开时隐藏对当前分组的重复操作。

## Non-Goals

- 搜索量柱状图、展示量、绝对点击量、PPC 竞价——事实表没有这些字段，不估算、不换算。
- 月排名——没有原始月报，不把日/周排名平均成月排名。
- 跨年对比、季节性判断、批量多词对比、详情专用导出。
- 修改 `HOT_WINDOW_MONTHS` 或保留清理策略；不新增趋势相关的长期归档。
- 自动判定持续增长、趋势分类、机会评分——不改变现有增长榜 (`fact_growth`) 算法。

## Capabilities

### New Capabilities
- `keyword-trend-detail`：单关键词的日/周排名历史查询契约、五态缺期分类、区间统计口径、
  权限与跨站点校验、两个入口的复用与加入词库集成。

### Modified Capabilities
- `analysis-screen-layout`：结果表新增打开趋势详情的入口（复用现有搜索趋势列）。
- `keyword-library`：盯盘表新增同一入口，复用同一份列投影带出的趋势列。

## Impact

**后端**：新增 `app/table/keyword/trend_schemas.py` / `trend_crud.py` / `trend_service.py` /
`trend_api.py`，挂载到既有 `/api/keyword` 前缀。复用 `app/table/analysis/analysis_crud.py`
的 `fact_and_datecol`、`available_dates_stmt`，以及 `app/table/analysis/partitioning.py` 的
`retention_cutoff`。`app/auth/simple_auth.py` 新增一个「任一权限点即可」的依赖工厂
（`require_any_permission`），供两个入口共用同一端点。`app/common/errors.py` 新增 2 个
错误码（关键词不存在 / 站点不匹配）。

**前端**：新增 `frontend/src/components/KeywordTrendDrawer/`（抽屉容器 + 时间轴图表 +
统计条 + 加入词库按钮），`frontend/src/services/keywordTrend.ts`（API 客户端）。
`frontend/src/pages/Analysis/columns.tsx` 与 `frontend/src/pages/Keyword/KeywordLibrary/columns.tsx`
的趋势列增加可点击入口（`onOpenTrend` 回调），由各自的 `index.tsx` 提供实现与抽屉状态。

**数据模型**：不新增表、不新增列、不新增迁移——全部查询走已有的
`idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期)` 与 `fact_dropped` 主键。

**文档**：CLAUDE.md 补一节记录端点契约、五态分类算法、权限的「任一满足」用法，供后续
类似的跨入口只读端点参照。
