## Context

现状均已对照仓库核实（2026-11-04，基于 `2558ea1`）：

| 方面 | 现状 |
|---|---|
| 页面 | `frontend/src/pages/Competitor/index.tsx` 有三个 Tab：亚马逊卖家 / 独立站 / 上新流，上方是共用的 `GroupBar`。路由 `/competitor` 是无子项的单页顶层，布局为 `mix` + `splitMenus` |
| Amazon 状态 | 「待建立基线 / 监控中」两态，由 `baseline_at` 是否为空决定；已有「近 7 日新发现」「最近采集于」两列和超过 7 天的陈旧提示，但没有采集方式说明，也没有上报条数 |
| 上新流 | `store_crud.new_arrivals_stmt` 在 SQL 里把 DTC 和 Amazon 两侧 `UNION ALL`，再统一排序分页。`NewArrivalRequest` 只有 `days`/`page`/`per_page`。Amazon 侧的 `source_name` 恒为 NULL，因为从未采集过卖家名。投影里已有 `image_url`，但前端没有展示 |
| 单批上限 | 服务端 `COMPETITOR_MAX_REPORT_ITEMS=200`，超限 422、不截断。插件里有镜像常量 200，运营也可以在 options 页手填。超限时插件会硬性阻止提交，因为分批提交会让首批落定基线，后续批次的存量商品被误判为新发现 |
| 写入 | 每次上报一条 `competitor_asin_report`（含 `item_count`），ASIN 用一条 `INSERT … ON CONFLICT DO NOTHING` 写入。nginx 的 `client_max_body_size` 是 100M |
| Shopify | 抓取、完整目录基线、价格/名次历史、四类失败状态、详情抽屉都已实现，本次不改其逻辑 |
| 分组 | `user_seller_group` 由 `user_seller_watch.group_id` 与 `user_store_watch.group_id` 共用，删除分组时成员的 `group_id` 置 NULL |

## Goals / Non-Goals

**Goals:**

- 竞对模块按运营任务组织：「商品发现」看新商品，「店铺管理」管关注对象和数据状态。
- Amazon 侧如实表达「手动采集」：未采集、已采集、陈旧三种状态能区分开，并说明上报条数不等于全店总量。
- 商品发现可以直接看品、打开原商品，筛选真正传到后端，列表与总数口径一致。
- 单卖家一次采集最多 1000 条，插件和服务端用同一个上限值，大店有明确提示。
- 现有 Shopify 链路保持不变，只改名称和展示。

**Non-Goals:**

- Amazon 自动巡店、服务端抓取或第三方数据源。
- 分批建立基线的协议（见 D4）。
- 修改 Shopify 的抓取频率、判定口径或表结构。
- 清理、重建现有基线或历史数据。
- 全站导航重构（只改竞对模块）。
- 商品到 ABA 关键词的联动（已由 #166 B 收回）。

## Decisions

### D1 导航：真实子路由加 Umi 自带侧栏，不在页面内另做一套菜单

```
/competitor            → redirect /competitor/discovery
/competitor/discovery  商品发现（默认）
/competitor/stores     店铺管理
```

`access: 'canViewCompetitor'` 挂在父节点上，子项继承。`mix` + `splitMenus` 布局下，有子项的顶层节点会自动长出可折叠的左侧栏，这正是 #166 想要的效果，所以不需要在页面里再用 `Layout.Sider` 做一个。

- 备选「页内 Menu + 同一路由」否决：折叠、高亮、刷新后停留位置都要自己实现，而且 URL 不反映当前任务，没法分享或收藏。
- 代价：竞对两个子页的内容区会少约 208px 宽度。这正是 `top-nav-structure` 当初禁止加侧栏的原因。接受这个代价的理由：两张主表都比分析查询屏窄得多，没有横向撑满的走势列；#166 也明确用任务菜单取代 Tab。规格上只把竞对监控移出「无子项单页」名单，分析查询屏和机会中心的约束不变。

### D2 店铺管理：一页内切换店铺类型，复用现有两张表

页内用 `Segmented` 切换「Amazon 卖家 / Shopify 店铺」，下面复用现有的 `columns.tsx` 卖家表和 `StoreTab` 店铺表，「添加」按钮跟着当前类型走。`GroupBar` 放在表格上方，两种类型共用（与现状一致）。切换类型的状态写在 URL 查询参数（`?type=amazon|shopify`），刷新后不丢。

- 备选「两个类型各占一个菜单项」否决：那样又回到按来源分菜单，正是 #166 要避免的层级混杂。

Amazon 行新增和调整的内容：

| 项 | 规则 |
|---|---|
| 采集方式 | 固定显示「手动采集」，悬浮提示「随你在浏览器里用插件采集更新，后台不会自动检查」。Shopify 行显示「自动检查」 |
| 状态 | 「未采集」：`baseline_at` 为 NULL。「已采集」：有基线且最近上报在 7 天内。「数据陈旧」：有基线但最近上报超过 7 天。阈值沿用 `NEW_DISCOVERY_WINDOW_DAYS`。状态由后端计算后下发（`status` 枚举改为 `NOT_COLLECTED / COLLECTED / STALE`），前端不再自己用时间推算 |
| 最近采集 | 时间，加上那次上报的 `item_count`。文案为「最近一次上报 N 条（不代表全店总量）」 |
| 操作 | 「打开商品列表」，用现有 `sellerLink` 打开；「采集说明」用 Popover 显示四步操作，并注明手动采集也有被站点限制访问的风险 |

最近一次上报的条数从 `competitor_asin_report` 取 `(seller_id, site)` 下最新一行，走现有索引 `idx_competitor_asin_report_latest`。和 `_last_reported_at_subq` 同一个子查询，只多取一列。

### D3 Amazon 店铺详情：只读列出已采集到的商品

新增 `POST /api/competitor/watches/{watch_id}/asins`，分页列出该卖家在该站点下的 `competitor_seller_asin`，按 `first_seen_at` 倒序。每行包含主图、标题、ASIN、首次发现于，并标注是否属于基线批次（`first_seen_at = baseline_at`）。

- 私有性：`watch_id` 必须属于当前用户，否则返回 404，与现有 `asin_detail_stmt` 一致。
- 不新增产品库、不新增表，只读取现有共享观测。Shopify 店铺的详情沿用现有的 `list_store_products` 抽屉。

### D4 单批上限 1000，插件从服务端读取，仍然一次提交

- `COMPETITOR_MAX_REPORT_ITEMS` 默认值从 200 改为 1000（`config.py`、`.env.example`）。
- 新增 `GET /api/competitor/collector-config`，权限要求 `competitor:view`，返回 `{max_report_items}`。插件每次初始化面板时读取。读取失败时显示「无法获取服务端上限」，并禁用「采集本页」，不再回落到本地常量：否则可能出现运营翻完很多页、提交时才收到 422 的情况。插件的 `MAX_REPORT_ITEMS` 常量和 options 页的手填上限字段一并删除。
- 超限时继续保持现有的硬性阻止，提示里写明「本轮支持单卖家最多 {cap} 条」，以及不能分批提交的原因。
- **不实现分批建立基线。** 那需要会话级的基线协议：一次采集会话的多次提交共享同一个基线时刻，会话结束后才落定。这是另一项契约变更，本轮不做。

容量核对：

| 环节 | 1000 条时 | 结论 |
|---|---|---|
| 请求体 | 标题与图片地址都在截断上限内，最坏约 2.5 MB | nginx 限制是 100M，FastAPI 不限 |
| 写入 | 一条 `INSERT … ON CONFLICT`，1000 行 × 7 列，约 7000 个绑定参数 | 低于 asyncpg 的 32767 上限，单事务即可完成 |
| 插件暂存 | `chrome.storage.local` 存 1000 条 | 远低于 10 MB 配额 |
| 基线正确性 | 仍是同一卖家第一次有效上报落定基线 | 语义不变，不需要迁移现有数据 |

本轮支持规模以真实页面验收为准（tasks 第 8 节记录实际站点和最大条数）。Amazon 店铺列表页的可翻页数可能存在上限，验收时如实记录，不宣称「任意规模可用」。

上线前要只读核对生产 `.env` 里是否覆盖了 `COMPETITOR_MAX_REPORT_ITEMS`。如果覆盖为 200，要由用户决定是否修改。

### D5 卖家店名：可选字段、最新非空值覆盖、不计入摘要

- 请求新增 `seller_name: str | None`，超长截断到 200，空白视为没有。
- 写入共享表 `competitor_seller.seller_name`。只有本次上报不是重放、且带了非空值时才更新，按最新值覆盖。这一点与 ASIN 的标题、图片「首次落定」不同，原因是店名属于卖家主体的当前展示属性，店铺改名后应当跟随。
- 不计入 `request_hash`，理由与标题、图片相同：属于易变的展示属性。
- 共享表依然不带任何账号属性，`seller_name` 是公开页面上的信息。
- 插件在 `parse-storefront` 里尽力解析店名，选择器未在真实页面验证过，取不到就不发送。界面依次回退：`seller_name`，没有就显示 `seller_id`。用户私有的备注不拿来顶替店名。

**迁移**：新增一个 Alembic 迁移，执行 `ALTER TABLE analysis.competitor_seller ADD COLUMN seller_name varchar(200) NULL`，带列注释。该表是普通表、不是分区表，PostgreSQL 加可空列且无默认值只改元数据，不会重写表，锁的时间极短。downgrade 删除该列。迁移用现有的 `DATABASE_SCHEMA` 写出 schema 名，与其他竞对迁移一致。不登记到事实保留清理。

### D6 商品发现：在 UNION 两侧各自加筛选，外层统一分页

`NewArrivalRequest` 新增以下可选参数：

- `source: 'AMAZON' | 'DTC'`
- `site`：只作用于 Amazon 侧；传了站点时 DTC 侧整体不返回，因为 Shopify 没有站点概念
- `group_id`：指定分组；不传时不按分组过滤，与卖家清单 `watches_stmt` 的现有语义一致（目前没有「未分组」这个筛选取值，本次也不新增）
- 单个店铺拆成两个扁平字段：`seller_id`（只剩 Amazon 侧，可与 `site` 组合定位某个站点的卖家）、`domain`（只剩 Shopify 侧，按加入时的同一套规则规范化）。不用嵌套的 `store` 对象：两类店铺的身份字段完全不同，嵌套之后只会多出一层「哪些键组合合法」的校验

每个筛选条件都下推到 `_dtc_arrivals_select` 和 `_amazon_arrivals_select` 两侧各自的 `WHERE` 里，被排除的一侧直接不参与 UNION。外层排序、分页、计数都基于同一个 UNION 子查询，与现有「同一次查询内合并」的规则一致。`group_id` 通过各自的 watch 表过滤，因此天然限定在当前用户名下。

投影新增以下列（两侧列名一致，不适用的一侧为 NULL）：

| 列 | Amazon | Shopify |
|---|---|---|
| `discovered_at` | `first_seen_at` | `first_seen_at` |
| `published_at` | NULL | `published_at` |
| `product_url` | `https://www.{site 域名}/dp/{asin}` | `https://{domain}/products/{handle}` |

Amazon 的域名从 `site_registry` 取。`at` / `time_kind` 保留，仍作为排序键和主时间。界面上 Amazon 行显示「首次发现于」；Shopify 行同时显示「上架于」（有值时）和「首次发现于」。

**性能**：两侧都从当前用户的 watch 表（几百到几千行）连接到 `competitor_seller_asin` / `competitor_store_product`，利用现有的 `(seller_id, site, first_seen_at)` 和 `(domain, first_seen_at)` 索引，筛选只会让结果集更小。不新增索引，不占用分析查询的并发闸，连接池占用和内存与现状同一量级。实现时用本地数据跑一次 `EXPLAIN` 确认仍走这两个索引。

### D7 命名：「独立站」改为「Shopify 店铺」，只改界面和文档

前端可见文案（约 25 处）、插件说明、规则和导航文档中，凡是表示当前接入平台的「独立站」，一律改为「Shopify 店铺」。后端枚举（`DTC`）、表名、接口路径（`/stores`）保持不变：改标识符会牵动迁移和调用方，没有业务收益。Shopify 的状态标签「监控中」改为「自动检查正常」，与 Amazon 的「手动采集」区分开。

## Risks / Trade-offs

- [侧栏占宽] 竞对子页少约 208px → 两张表本来就窄，且菜单可折叠。已在 D1 写明理由，分析屏的约束不受影响。
- [店名选择器未验证] Amazon `me=` 页可能根本没有稳定的店名元素 → 取不到就不发送，界面回退到 seller_id。真实页面验收时记录能否取到，取不到也不阻塞本变更。
- [上限读取失败就禁用采集] 后端暂时不可用时，运营无法开始采集 → 这比翻完很多页后才失败更可控，提示文案会说明原因。
- [生产覆盖了上限] 如果生产 `.env` 显式写了 200，改默认值不会生效 → 发布前只读核对，由用户决定。
- [状态枚举改名] `WatchStatus` 的取值从 `PENDING_BASELINE / MONITORING` 改为三态，属于前后端同版本契约 → 前后端一起发布；旧前端拿到未知值会回落到「未采集」（现有代码已有兜底）。
- [手动采集风控] 大批量翻页可能触发 Amazon 的访问限制 → 在说明里如实写出，不宣称没有风险；真实验收时记录现象。
- [规模上限] Amazon 店铺列表页可能只能翻有限页数 → 验收记录实际可采集的上限，不宣称任意规模可用。

## Migration Plan

1. 先合并迁移和后端：新增的列与端点都是加法，旧插件不带 `seller_name` 时照常可用。
2. 前端同版本发布：路由、状态三态、商品发现新字段。
3. 插件发新版本 zip：从服务端读上限、上报店名。旧版插件继续可用，但仍按本地 200 或手填值拦截，所以大店需要更新插件。安装说明要同步更新。
4. 回滚：代码回退即可；迁移的 downgrade 删除 `seller_name` 列，只会丢失店名这一展示信息。

## Open Questions

- 生产 `.env` 是否覆盖了 `COMPETITOR_MAX_REPORT_ITEMS`：发布前只读核对（tasks 9.1）。
- 插件安装包给运营的分发位置：现有 `docs/extension-install.md` 是仓库内文档，界面里的「采集说明」只能写文字步骤，安装包仍按现有方式由管理员发放。如果希望界面内能直接下载，需要另行决定托管位置。
