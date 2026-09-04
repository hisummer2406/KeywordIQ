## Why

issue #65 记录了一个从 `add-competitor-dtc-monitoring` 评审时就发现的缺口：上新流
`NewArrivalsTab` 从一开始就没有"点开一行看详情"的交互——不是 Amazon 分支特有的，
独立站同样没有。`competitor-storefront-watch` spec 现有的 Requirement 里明确写着
"点开一行查看详情不在本变更范围内"。

issue #65 把这个缺口拆成两半，两半的可动工程度不一样：

- **独立站**：数据早就有了。`competitor_store_price` 每次调价都追加一行，
  `product_history` 服务与端点（`store_service.py:697`、`GET /stores/{domain}/
  products/{product_id}/history`）、前端 `getProductHistory()` 客户端函数都已存在
  且没有任何改动——**这半只缺 UI 入口**。
- **Amazon**：卡着一个真实的设计决策（"最近一次采集时刻"要不要新增条目级明细表，
  还是用卖家级 `last_reported_at` 近似），issue 原文写明"需要评估这个近似是否可接受"。

本变更**只做独立站那半**。Amazon 那半继续留在 issue #65，不在这里因为要对称而强行
一起做。

## What Changes

- `NewArrivalsTab.tsx`：DTC 行新增点击交互，打开 Drawer 展示该商品的变体价格与
  店内名次历史。Amazon 行点击行为不变（本变更不涉及）。
- 新增 `ProductDetailDrawer.tsx`，调用既有的 `getProductHistory(domain, productId)`。
- **展示形式选表格，不选图表**，理由记在这里而不是另开 design.md：
  - 价格历史"仅在取值变化时追加"，观测点在时间轴上**不均匀分布**——可能几周只有
    一个点，也可能一天内调价两次。现有的 `TrendSparkline` 组件是为等距点（日视图
    7 点/周视图 4 点）设计的定长布局，直接复用会把不均匀的时间压缩成等距点，
    误导"调价频率"这件事本身。
  - 数据源没有强制"多列多值"的比较需求（不像分析屏要横向比较多行），表格的
    「时间 / 变体 / 价格 / 划线价 / 是否在售」原样陈列已经把信息说清楚，不需要
    为此新建一个变长时间轴的图表组件。
  - 引入新图表能力的成本（时间轴刻度、悬浮交互、稀疏点位的视觉设计）与本次要解决
    的问题（"完全看不到历史"→"能看到历史"）不成比例。
- 名次历史（`ranks[]`，店内相对名次、按日）同样用表格，不新建图表——**MUST NOT
  呈现为销量或跨店比较**，这是 `competitor-storefront-watch` 既有约束，抽屉里的
  文案要延续同一措辞。

## Capabilities

### Modified Capabilities

- `competitor-storefront-watch`：Requirement「上新流合并两类来源，但必须标注时间
  语义的差异」——移除"点开一行查看详情不在本变更范围内"这句对独立站不再成立的话，
  改为指向下面新增的 Requirement；Amazon 侧的缺口原样保留，指向 issue #65。三条
  既有场景不变。

### New Capabilities

- `competitor-storefront-watch`：新增 Requirement「点开一行查看独立站商品详情」。

## Impact

**受影响文件**（全部在 `frontend/src/pages/Competitor/` 下，后端零改动）：
- 新增 `ProductDetailDrawer.tsx` 及其测试
- `NewArrivalsTab.tsx`：加点击交互

**不受影响**：后端全部（`product_history` 端点与服务早已存在且不改）、Amazon 行为、
`StoreTab.tsx`、其余竞对监控页面。
