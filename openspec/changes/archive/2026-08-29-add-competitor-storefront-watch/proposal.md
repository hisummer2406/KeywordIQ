## Why

运营盯竞对上新目前是纯人肉：在浏览器里收藏大量
`https://www.amazon.com/s?i=merchant-items&me=<卖家ID>&s=date-desc-rank` 链接，逐个点开看
店铺最近上了什么新品。一份实际导出的收藏夹（`docs/bookmarks_2026_8_18(1).html`，
2024-03 ~ 2026-08，2026-08-29 用真实文件核实过）里有 3177 条书签、覆盖 2657 个不同卖家 ID，
散在 94 个嵌套文件夹里。这些链接散在浏览器里，不可查询、不可排序、无历史、无预警，换台
电脑或换个人接手就全丢了。来源 issue #56。

把「批量盯竞对上新」产品化进 KeywordIQ 的价值在于**与现有 ABA 关键词数据打通**——这是相对
通用选品工具的独有优势（`fact_daily`/`fact_weekly` 的 `top1/2/3_product_id` 与
`top1/2/3_brand_id` 可以反查竞对新品正在抢占哪些搜索词）。但打通查询（P2）与自动化数据获取
（浏览器插件/Keepa，P3）依赖运营先有一份结构化的「我在盯哪些卖家」清单——这份清单本身
（分组管理 + 导入 + 状态跟踪）不依赖任何未决项，可以独立交付。

**本变更范围严格限定在这份清单**：数据模型、书签/文本导入解析、预览确认两步流、权限点、
管理页。**不包含**数据获取层（浏览器插件、Keepa）、「今日上新」页、与分析屏的双向联查——
那些留给后续变更（对应 issue #56 phasing 表的 P3a/P1/P2/P3b/P3c）。

## What Changes

- **新增两张私有层表**：`user_seller_group`（分组，`UNIQUE(user_id, name)`）、
  `user_seller_watch`（关注关系，`UNIQUE(user_id, seller_id, marketplace)`，挂
  `group_id`/`priority`/`note`）。
- **新增一张共享事实层表** `competitor_seller`（`UNIQUE(seller_id, marketplace)`，含
  `baseline_at` 列，为后续数据获取层的「首刷不算新品」问题预留位置，本变更不写入
  非 NULL 值——没有数据获取层就没有首刷）。
- **新增一张技术表** `competitor_watch_preview`（预览结果暂存，替代引入 Redis）。
- **新增 `SellerRefParser`** 纯函数解析器：从书签 HTML / 粘贴文本中提取 `seller_id` +
  `marketplace`，容错处理长 URL、裸 ID、不支持的链接形态。
- **新增预览→确认两步接口**：`POST /api/competitor/watch/preview`（只解析不落库）、
  `POST /api/competitor/watch/commit`（按 `preview_token` 落库，`append` 或
  `replace_group` 两种模式）。
- **新增分组与关注关系的增删改查接口**：建组/改名/删组、移除关注、改分组、改备注。
- **新增权限点 `competitor:view`**（不复用 `toolbox:view`——每个运营的收藏池互相独立，
  与工具箱现有屏幕不是同一授权粒度），授予 admin/operator/analyst 三个内置角色。
- **新增顶层菜单页「竞对监控」**（`frontend/src/pages/Competitor/`，AntD Pro + ProTable），
  分组管理区（表格上方，不做侧栏）+ 主表格（卖家、站点、分组、状态、配额提示）+
  「添加卖家」（textarea 批量粘贴）+「导入书签」（`.html`）两个入口。

## Capabilities

### New Capabilities

- `competitor-storefront-watch`: 竞对卖家关注清单的数据模型（共享卖家主体 + 私有分组/
  关注两层）、`SellerRefParser` 解析口径、预览/确认两步导入语义、配额、权限点、管理页
  交互。

## Impact

- **新增代码**：`app/table/competitor/{seller_model,watch_model,watch_crud,watch_service,
  watch_api,watch_schemas,seller_ref_parser}.py`；前端 `frontend/src/pages/Competitor/`。
- **改动代码**：`app/auth/permissions.py`（新增权限点常量）、`frontend/src/access.ts`
  （`canViewCompetitor`）、`frontend/.umirc.ts`（新增顶层菜单项）、`migration_scope.py`
  （登记三张新表）、`main.py`（注册路由）。
- **数据库**：一条 Alembic 迁移建三张普通表（均可 autogenerate，非分区、非物化视图）+
  种入权限点与角色授权（仿 `f2a3b4c5d6e7` 的写法）。
- **不影响**：导入路径（`fact_daily`/`fact_weekly` 写入）、既有分析查询与导出、工具箱
  既有屏幕、`toolbox:view` 的权限范围。
- **明确不做**（留给后续变更，见 issue #56 phasing）：`competitor_asin`/
  `competitor_asin_snapshot`（数据获取层落地时再建，本变更不产生任何 ASIN 级数据）、
  浏览器插件、Keepa 集成、「今日上新」页、与 `dim_product`/`dim_brand` 的双向联查、
  `top2/top3_product_id` 补索引。
