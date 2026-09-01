## Why

竞对监控的 Amazon 卖家侧至今**没有任何商品数据**：`competitor_seller.baseline_at` 恒为
NULL、界面状态恒为「待建立基线」、「近 7 日上新数」是占位符，上新流里只有独立站一类来源。
issue #56 的分阶段表把浏览器插件被动采集（P3a）排在最前，正是因为后面每一段都等它产出数据
——「今日上新」页（P1）与「与 ABA 关键词打通」（P2，方向 A/B 按 ASIN 回查
`top1/2/3_product_id`）都建在这份 ASIN 数据之上，没有它就是建在空地基上。

本变更做 P3a 的**服务端侧**：把「卖家 → 在售 ASIN」这份数据的落库、上新判定与查询做完，
形成一份插件可以照着写的稳定契约。**采集端（Chrome 插件本体）不在本变更范围**，届时它只
需要按本契约 POST。

## What Changes

- **新增两张普通表**：`competitor_seller_asin`（卖家 × ASIN，只增不改，承载 `first_seen_at`）
  与 `competitor_asin_report`（上报批次的观测元数据：谁、何时、哪个卖家、多少条、
  采集时的配送地区）。两张都是普通表，**MUST NOT 登记进 `PARTITIONED_TABLES`**。
- **不建 issue 里写的 `competitor_asin_snapshot` 分区表**（见 design 决策 2）。它的前提是
  「定期全量抓取」，而被动采集是**部分捕获**，逐次快照记录的是「运营那天翻了几页」而不是
  「这家店有多少品」。
- **新增上报端点** `POST /api/competitor/asin-reports`（权限点沿用 `competitor:view`）：
  推模型，幂等，单次条数超限整批拒绝；只接受**上报者本人已关注**的卖家。
- **基线落定**：某卖家的第一次上报确立 `baseline_at`，该批 ASIN 只入库、不进上新流；
  之后 `first_seen_at > baseline_at` 的才是上新。与独立站的 `first_fetch_at` 同构。
- **不做下架判定**：被动采集拿到的目录恒不完整，这条与独立站「下架判定只在
  `catalog_complete` 为真时做」是同一条纪律的必然结论。
- **上新流接入 AMAZON 来源**：`POST /api/competitor/new-arrivals` 由单一来源改为合并两类，
  Amazon 行的时间语义是「发现于」（`DISCOVERED`）。响应形状不变——它当初就是按两类来源
  设计的。
- **卖家清单的状态与「近 7 日上新数」变成真值**：有基线的卖家显示「监控中」与真实条数，
  仍无上报的继续显示「待建立基线」与占位符。
- 本变更**不涉及日报路径，也不涉及周报路径**——不碰 ABA 导入、事实表与查询层。与 ABA
  关键词的联查（issue #56 的 P2）是独立的下一步。

## Capabilities

### New Capabilities
- `competitor-asin-ingest`: 竞对卖家在售 ASIN 的上报采集——推模型的端点契约、幂等与配额、
  基线判定、不做下架判定的边界，以及上报数据新鲜度的呈现要求。

### Modified Capabilities
- `competitor-storefront-watch`: 「关注状态展示待建立基线」由「恒为待建立基线」放宽为
  「按有无基线分两态」；「上新流合并两类来源」的 Amazon 那一侧由预留形状变为真实数据。

## Impact

- **数据模型**：`app/table/competitor/asin_model.py`（新）；`migration_scope.py` 的
  `V2_TABLES` 登记两张新表；一条 Alembic 迁移（普通表，可 autogenerate 后核对）。
- **后端**：新增 `asin_crud.py` / `asin_service.py` / `asin_api.py`；
  `store_service.new_arrivals` 与 `store_crud.new_arrivals_stmt` 改为合并两类来源；
  卖家清单查询补上新数。
- **前端**：`pages/Competitor` 的卖家列（状态、近 7 日上新数）与 `NewArrivalsTab` 的
  AMAZON 分支由占位改为真实数据。
- **不影响**：ABA 导入与查询、保留清理、分区管理、权限矩阵（不新增权限点）。
- **后续依赖本契约**：Chrome 插件（P3a 采集端）、今日上新页细化（P1）、ABA 联查（P2）。
