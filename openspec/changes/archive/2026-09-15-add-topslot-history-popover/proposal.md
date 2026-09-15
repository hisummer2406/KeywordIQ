## Why

市场分析表格已经展示当前关键词的「点击前三 ASIN」与「点击前三品牌」，但用户看到的只是
当前这一期的静态快照——某个 ASIN／品牌在这个关键词下历史上出现过多少期、各期处于第几名，
完全无从得知。运营判断一个商品/品牌是稳定占位还是刚冲上来又要掉下去，恰恰需要这段历史。
issue #96 要求为这两列各增加一个悬浮历史面板，只读，不改变现有列的呈现与交互。

## What Changes

- 新增只读端点：给定 `site + view + keyword_id + on`（来源页面已生效的查询作用域）与
  一个身份（ASIN 原文，或品牌原文），返回该身份在这个关键词下、`on` 之前保留窗口内
  实际进入点击前三的期次列表（日期 + 当期名次），按日期倒序，支持分页。
- 查询范围固定为「单一站点 + 单一 keyword_id + 单一视图」，与既有的 `fact_and_datecol`
  规则一致：日视图查 `fact_daily`，周视图查 `fact_weekly`。
- 起始日期取 `partitioning.retention_cutoff`，不早于保留策略仍能查到的下界；不做
  `keyword-trend-detail` 那种五态覆盖率枚举，只列出实际命中的期次。
- 上榜次数一次性精确统计（不是本页行数），且与分页无关；同一期同一身份最多计一次，
  重复占槽时确定性取最佳（数值最小）名次。
- 前端新增一个悬浮历史面板组件，接入「点击前三 ASIN」卡牌列与「点击前三品牌」列：
  悬浮才发起首次请求，面板内可滚动，触底追加下一页，请求带竞态防护（切换查看对象或
  作用域后旧响应不得覆盖新内容）。
- 本变更**不影响**导入链路、事实表结构、保留策略；只新增一条只读查询路径。

本变更同时影响日报（`fact_daily`）与周报（`fact_weekly`）路径——查询按来源页面当前
所在视图二选一，不会同时查两张表。

## Capabilities

### New Capabilities
- `topslot-history-popover`：市场分析表格里 ASIN／品牌的悬浮历史查询能力（只读端点 +
  前端悬浮面板），与 `keyword-trend-detail`（关键词整体排名走势）平级，是两个不同粒度
  的历史查询能力，不合并。

### Modified Capabilities

（无——不改变任何既有 capability 的需求级行为，`analysis-screen-layout` 里「三个位置
固定对应第一二三名」「份额 0~100 不换算」等既有约束原样保留，本变更只是在其上新增交互
入口。）

## Impact

- 后端新增：`app/table/analysis/topslot_history_crud.py`（查询组装）、
  `topslot_history_service.py`（站点/参数校验、身份解析、分页与计数）、路由挂载到既有
  `analysis_api.py` 或新增 `topslot_history_api.py`（沿用 `analysis:view` 权限点，
  `simple_auth.require_permission`）。
- 数据库：不新增表、不新增列、不新迁移——复用 `fact_daily`/`fact_weekly` 现有的
  `idx_fact_{daily,weekly}_keyword_history (site, keyword_id, 日期列)` 索引与
  `dim_product.asin` / `dim_brand.name` 的既有唯一约束。
- 前端新增：一个可复用的悬浮历史面板组件，接入
  `frontend/src/pages/Analysis/TopProductCards.tsx` 与
  `frontend/src/pages/Analysis/columns.tsx`（品牌列，经 `RankedList.tsx`）；新增
  `frontend/src/services/` 下对应的请求函数与类型。
- 测试：后端 crud/service 单元测试 + 一条集成测试（真实分区表、真实索引路径）；前端
  面板组件测试（分页、竞态、五种状态）。
