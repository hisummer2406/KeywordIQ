## Why

竞对监控的 P0（`competitor-storefront-watch`）只建了关注清单本身，没有任何商品数据——
界面上「近 7 日上新数」恒为占位符、状态恒为「待建立基线」。原计划的数据获取层（浏览器
插件采集 Amazon 店铺页、或 Keepa）至今都卡在未决前提上：插件的分发方式、令牌交接、
Keepa 的 token 扣费，以及运营的 Amazon 账号必须先设成美国收货地址。

2026-08-31 实测发现，书签里那部分**独立站**存在一条完全不同的、当下就能走通的路径：

- 书签里的 17 个独立站域名 **17/17 在线，全部是 Shopify**，16 个 `/products.json` 公开可取
- 这个端点直接给 **`published_at`（上架时间，精确到分钟）**、`created_at`、全部变体的
  `price` / `compare_at_price` / `sku` / `available`
- 翻页可取**全量目录**（实测一家 1202 个商品、5 页），不是 Amazon 那种只能看第 1 页
- `/collections/all?sort_by=best-selling` 的畅销排序在 HTML 页上生效（与上新序前 20 名
  0/20 重合），提供店内相对销量名次
- 全程**服务端 HTTP 请求**，不需要浏览器、插件、用户登录或令牌

关键差异在于「上架时间是数据源直接给的事实」——Amazon 侧实测 5 个 ASIN 全部拿不到
「Date First Available」，只能靠两次观测的差集推断，整套 `baseline_at` 设计就是为绕开
这一点而存在的；独立站这条路**首刷即有历史**，一条基线都不用建。

因此本变更先做独立站这一条：它没有未决前提，写完当天就有数据，并且顺带把「上新」这块
界面立起来，供后续的 Amazon 插件变更复用。

**本变更既不影响日报路径也不影响周报路径**——它不读写 `fact_daily` / `fact_weekly` /
`dim_*`，不参与 ABA 报表的导入与查询，只在竞对监控这个数据域内新增表与拉取任务。

## What Changes

- 新增**独立站主体与关注关系**：用户粘贴域名即可加入监控，与现有的 Amazon 卖家关注
  并存于**同一批分组**（`user_seller_group` 已是站点无关的标签，无需改动）
- 新增**服务端定时拉取**：挂在 `import_queue.py` 已有的轮询循环上，按 `last_fetch_at`
  状态列认领到期站点（`FOR UPDATE SKIP LOCKED`），**不新开调度器、不引入 Celery/Redis**
- 新增**三类监控数据，三种写入模式**（粒度与写入节奏都不同，故分表存储）：
  - 商品主体：只增不改，记 `published_at` / `first_seen_at` / `delisted_at`
  - 价格：**变更日志，只在与上一条不同时追加**（全量日快照是 30 倍的写放大）
  - 销量名次：每日快照，只存前 N 名
- 新增**完整性标记**：下架判定仅在整次目录拉取完整成功时才做；拉取不完整只做并集写入，
  绝不判下架
- 竞对监控页新增独立站分段与「上新」流；**「上新」流合并展示两类来源，但 Amazon 侧的
  时间标注为「发现于」而非「上架于」**
- **不新增权限点**，沿用 `competitor:view`
- **不含 Amazon 浏览器插件采集**（另立变更），因此 Amazon 卖家行的状态与占位符保持现状

## Capabilities

### New Capabilities

- `dtc-store-monitoring`: 独立站（Shopify）商品监控——站点关注、服务端定时全量拉取、
  新品/价格/销量名次三类数据的采集与存储、完整性与失败状态的呈现

### Modified Capabilities

- `competitor-storefront-watch`: 两条要求需要放宽范围
  - 「分组归属用户，不绑定站点」——分组现在同时承载 Amazon 卖家关注与独立站关注两类
    成员，不再只是卖家
  - 「关注状态展示待建立基线，不隐含未实现的数据获取能力」——该要求需明确限定于
    **Amazon 卖家行**；独立站行有真实数据源，其状态与「近 7 日上新数」必须展示真实值，
    继续显示占位符反而成了谎报

## Impact

**新增代码**

- `app/table/competitor/store_model.py`：四张新表的 ORM 模型
- `app/table/competitor/store_fetcher.py`：纯函数，HTTP 响应文本 → 结构化数据；
  不连库、不连网，用固定 fixture 单测（与 `seller_ref_parser.py` 同一模式）
- `app/table/competitor/store_crud.py` / `store_service.py` / `store_api.py`
- 前端 `pages/Competitor/` 新增独立站分段与上新流

**修改代码**

- `app/table/upload/import_queue.py`：轮询循环新增一件事（与已有的清理过期竞对预览、
  回收超时直传批次并列）
- `app/table/analysis/migration_scope.py`：四张新表登记进 `V2_TABLES`
- `frontend/src/pages/Competitor/`：分组可容纳两类成员；状态列按成员类型分流

**数据库**

- 一条 Alembic 迁移，建四张**普通表**（`competitor_store` / `competitor_store_product` /
  `competitor_store_price` / `competitor_store_rank`）
- **MUST NOT 登记进 `partitioning.PARTITIONED_TABLES`**——登记即被保留清理按
  `HOT_WINDOW_MONTHS=3` 整分区 DROP，而价格与名次的历史越长越有价值；量级上（17 家店
  约 110 万行/年）也不需要分区

**配置**

- 新增 `DTC_FETCH_INTERVAL_HOURS` / `DTC_MAX_PAGES` / `DTC_RANK_TOP_N` /
  `DTC_REQUEST_DELAY_MS`，同步 `.env.example`

**依赖**：无新增。`httpx` 已在依赖里（`dingtalk_client.py` 已使用 `AsyncClient`）。

**不受影响**：ABA 导入路径（日报与周报）、分析查询屏、关键词工具、权限矩阵、
Amazon 卖家侧的关注与导入流程。
