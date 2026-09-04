## Why

issue #65 的 Amazon 那半：卡在一个数据可得性问题——要展示"这个 ASIN 最近一次采集时刻"，
当前 schema 只在 `CompetitorSellerAsin.first_report_id` 记了**首次**引入它的那次上报，
没有条目级的"每次出现在哪些上报里"记录；`competitor_asin_report` 本身也没有条目级明细。

issue 原文给了两条路：卖家级 `last_reported_at` 近似值，或新建一张 insert-only 的条目级
明细表（每次上报的每个 ASIN 都插一行）。

**本变更选近似值。** 理由：

- `competitor_seller_asin` 是**共享层、只增不改**的既有约束（一个人写错全体看见）。
  近似值方案不碰这张表，天然符合约束；新建明细表虽不违反"只增不改"（它是另一张新表），
  但引入的写入量与上报量同阶——单次上报最多 `COMPETITOR_MAX_REPORT_ITEMS`（默认 200）个
  ASIN，就是 200 行明细写入，且插件还没真机铺开，量级收益无法验证。
- 卖家级 `last_reported_at` 的查询早就存在（`watch_crud._last_reported_at_subq()`，
  服务于卖家清单的"最近采集于"列），复用它不新增查询复杂度。
- 唯一代价是这个时刻**不是该 ASIN 专属的**——同一卖家名下的其他 ASIN 也共享这同一个
  时刻。这个代价必须在界面上说清楚，不能让用户误读成"这个商品是这时候被采集的"。

## What Changes

- 新增只读端点 `GET /api/competitor/watches/{seller_id}/{site}/{asin}/detail`：
  返回该 ASIN 的 `first_seen_at`（真实数据，`CompetitorSellerAsin` 已有）与
  `seller_last_reported_at`（近似值，复用 `_last_reported_at_subq()`）。私有性由
  `UserSellerWatch` 的 join 保证——不是当前用户关注的卖家，返回 404。
- 前端新增 `AsinDetailDrawer.tsx`：点开上新流里一条 Amazon 行时打开，两行文字——
  「首次发现于 X」（真实事实）与「该卖家最近一次采集于 Y，不代表这个商品本身的
  采集时刻」（近似值，措辞必须包含这句免责说明，不能只给一个时间戳）。
- `NewArrivalsTab.tsx`：Amazon 行从"点击无反应"改为打开 `AsinDetailDrawer`；
  DTC 行沿用 `add-dtc-product-detail`（PR #74）已接好的 `ProductDetailDrawer`，不改动。
- **MUST NOT 展示 BSR 与评价数**——这条既有约束原样保留，本变更不碰。

## Capabilities

### Modified Capabilities

- `competitor-storefront-watch`：Requirement「上新流合并两类来源，但必须标注时间语义
  的差异」——移除"Amazon 侧仍未实现...跟踪见 issue #65"，改为指向下面新增的 Requirement。
  三条既有场景标题不变。

### New Capabilities

- `competitor-storefront-watch`：新增 Requirement「点开一行查看 Amazon 卖家 ASIN 详情」。

## Impact

**受影响文件**：
- `app/table/competitor/watch_crud.py`：新增 `asin_detail_stmt`
- `app/table/competitor/watch_service.py`：新增 `asin_detail`
- `app/table/competitor/watch_schemas.py`：新增 `AsinDetailResponse`
- `app/table/competitor/watch_api.py`：新增一个 `GET` 端点
- `frontend/src/pages/Competitor/AsinDetailDrawer.tsx`（新增）+ 测试
- `frontend/src/pages/Competitor/NewArrivalsTab.tsx`：Amazon 行接入点击

**不受影响**：`competitor_seller_asin`/`competitor_asin_report` 的表结构与写入路径、
DTC 那半（`ProductDetailDrawer.tsx`）、上新流主列表与分页逻辑。
