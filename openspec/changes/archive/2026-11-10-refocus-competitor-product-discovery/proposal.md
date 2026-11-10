## Why

#166 的核心诉求是：运营收藏竞对店铺后，能持续看到这家店新出现的商品，方便选品。现有竞对监控有四个问题：
- 「亚马逊卖家 / 独立站 / 上新流」三个 Tab 混排，其中两个按来源分、一个按任务分；
- Amazon 卖家显示「监控中」，实际只在人工采集后才会更新；
- 插件单批 200 条上限挡住了大店；
- 上新流只有标题和时间，看不到商品图和原商品链接。

这些让模块很难作为兜底方案使用。本变更是 #166 拆出的 A；市场分析解耦（B）已在 `retire-analysis-competitor-filter` / PR #167 完成。

## What Changes

- **BREAKING（导航）** 竞对监控从无子菜单的单页改为左侧任务菜单，只设「商品发现」（默认）和「店铺管理」两项，菜单可折叠。旧入口 `/competitor` 重定向到商品发现。权限仍只用 `competitor:view`，不新增逐页权限。
- **商品发现**（原「上新流」）：
  - 后端新增四种筛选：店铺、分组、来源（Amazon / Shopify）、Amazon 站点；时间范围沿用原有参数。所有筛选都在同一个 UNION 查询里施加，列表和总数口径一致。
  - 每行展示主图、标题、来源店铺、平台/站点、ASIN 或 Shopify 商品标识、首次发现时间、可获取的源上架时间，以及原商品链接。缺失的字段如实降级显示，不编造。
- **店铺管理**：Amazon 卖家和 Shopify 店铺放在同一页，通过店铺类型切换；分组仍放在表格上方。
  - Amazon 行展示「手动采集」：状态改为「未采集 / 已采集 / 数据陈旧」三态，同屏显示最近一次采集时间和那次上报的条数，并注明不代表全店总量。
  - 行内可以打开卖家的 Amazon 商品列表，并查看采集步骤说明。
  - 店铺详情可以查看已采集到的商品。
- **Shopify 命名**：所有表示当前接入平台的「独立站」改为「Shopify 店铺」，并标明「自动检查」。现有抓取调度、基线、价格/名次历史、失败状态及详情保持不变，也不提高抓取频率。
- **Amazon 单批上限**：默认值从 200 提到 1000，仍是一次提交，超限仍整批拒绝、不截断。
  - 插件改为从新的只读端点读取服务端的实际上限，删除 options 页里手填上限的字段。
  - 超过 1000 条的店铺会在插件里提前说明「本轮不支持」及原因。
- **卖家店名**：上报请求可以带可选的 `seller_name`，写入共享卖家表的新列。它与标题、图片一样，不计入幂等摘要。插件尽力从页面读取，取不到时界面回退显示 seller_id。
- **不做**：Amazon 自动巡店或服务端爬虫、第三方数据源、Shopify 以外的平台、销量/库存/BSR 等新数据来源、外部提醒、清空历史或重建基线、逐次目录快照。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `top-nav-structure`：「单页顶层菜单不得因分组而长出侧栏」把竞对监控移出无子项单页的列表；新增竞对监控任务菜单的要求。
- `competitor-storefront-watch`：
  - Amazon 状态从两态改为采集方式加三态；
  - 上新流更名为商品发现，补充筛选、看品字段和原商品链接；
  - 分组区约束改为在任务菜单下的页面内适用；
  - 新增「店铺管理按类型切换」「Amazon 店铺详情列出已采集商品」「平台命名为 Shopify 店铺」。
- `competitor-asin-ingest`：输入约束新增可选的 `seller_name`（截断，不计入摘要）；新增「服务端公布单批上限」。
- `competitor-browser-collector`：新增「单批上限从服务端读取」「尽力采集卖家店名」；超限提示需写明本轮支持的规模。
- `dtc-store-monitoring`：「独立站监控的目的与边界」补充界面命名，并说明不支持 Shopify 以外的平台。

## Impact

- **日报 / 周报路径**：都不受影响。本变更不涉及 ABA 事实表、导入或分析查询。
- **后端**：
  - `app/table/competitor/` 下的 `store_schemas`、`store_crud`、`store_service`、`store_api`（商品发现筛选与字段）；
  - `watch_crud`、`watch_service`、`watch_schemas`（状态、最近上报条数、店名、Amazon 店铺商品列表）；
  - `asin_schemas`、`asin_service`、`asin_crud`（`seller_name` 写入）；
  - `seller_model`（新列），新增一个只读的采集端配置端点；
  - `config.py` 与 `.env.example` 中 `COMPETITOR_MAX_REPORT_ITEMS` 的默认值。
- **迁移**：一次 Alembic 迁移，给 `analysis.competitor_seller` 加可空列 `seller_name`。
- **前端**：`frontend/src/routes.ts`；`frontend/src/pages/Competitor/` 拆成商品发现、店铺管理两个子页（复用现有表格、抽屉、分组栏、导入弹窗）；`frontend/src/services/competitor.ts`。
- **插件**：`extension/src/` 的 `content-script`、`auth`（删除手填上限）、`parse-storefront`（店名）、`build-report`；`extension/options.html`；`docs/extension-install.md`。
- **规则与文档**：`.claude/rules/competitor.md`、`openspec/guides/competitor.md`，以及 AGENTS「竞对监控」审核投影中涉及命名和状态的条目。
- 不影响 V1，不新增 Redis/Celery、消息渠道或后台巡检。
